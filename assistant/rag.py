"""Local RAG: chunk + embed uploaded docs via Ollama, hybrid BM25+cosine search.

Two ingest paths share one store and one search index:
- `ingest()`      — a file uploaded through the UI, stored under its filename
- `sync_vault()`  — every .md note in the Obsidian vault, stored as "vault:<rel path>"

Everything runs locally against Ollama's embedding model; nothing leaves the machine.
"""
import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
import ollama
from pypdf import PdfReader
from rank_bm25 import BM25Okapi

STORE_FILE = Path(__file__).resolve().parent.parent / "data" / "embeddings.json"
EMBED_MODEL = "nomic-embed-text"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
MIN_SIMILARITY = 0.5
RRF_K = 60

# Obsidian vault indexed alongside uploaded documents. Override with the
# OBSIDIAN_VAULT env var to point at a different vault.
VAULT_DIR = Path(os.environ.get("OBSIDIAN_VAULT", Path.home() / "brain"))
VAULT_PREFIX = "vault:"
VAULT_SKIP_DIRS = {".obsidian", ".trash", ".git", "templates"}

_TOKEN_RE = re.compile(r"\w+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _load_store() -> list[dict]:
    if not STORE_FILE.exists():
        return []
    return json.loads(STORE_FILE.read_text(encoding="utf-8"))


def _save_store(store: list[dict]) -> None:
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STORE_FILE.write_text(json.dumps(store), encoding="utf-8")


def _extract_text(filename: str, raw: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(__import__("io").BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return raw.decode("utf-8", errors="ignore")


def _split_sentences(text: str) -> list[str]:
    pieces = re.split(r"(?<=[.!?])\s+|\n\s*\n", text)
    return [p.strip() for p in pieces if p.strip()]


def _chunk(text: str) -> list[str]:
    sentences = _split_sentences(text)
    if not sentences:
        return []
    chunks = []
    current: list[str] = []
    current_len = 0
    for sent in sentences:
        if current_len + len(sent) > CHUNK_SIZE and current:
            chunks.append(" ".join(current))
            overlap: list[str] = []
            overlap_len = 0
            for s in reversed(current):
                if overlap_len + len(s) > CHUNK_OVERLAP:
                    break
                overlap.insert(0, s)
                overlap_len += len(s)
            current = overlap
            current_len = overlap_len
        current.append(sent)
        current_len += len(sent)
    if current:
        chunks.append(" ".join(current))
    return chunks


def ingest(filename: str, raw: bytes) -> str:
    text = _extract_text(filename, raw)
    if not text.strip():
        return f"No extractable text in {filename}."
    chunks = _chunk(text)
    store = [c for c in _load_store() if c["source"] != filename]  # replace prior version
    for chunk in chunks:
        emb = ollama.embeddings(model=EMBED_MODEL, prompt=chunk)["embedding"]
        store.append({"source": filename, "text": chunk, "embedding": emb})
    _save_store(store)
    return f"Ingested {filename}: {len(chunks)} chunks."


def _vault_files() -> list[Path]:
    if not VAULT_DIR.exists():
        return []
    return [
        p for p in VAULT_DIR.rglob("*.md")
        if not VAULT_SKIP_DIRS & set(p.relative_to(VAULT_DIR).parts)
    ]


def sync_vault() -> str:
    """Indexes every .md note in the vault, skipping notes whose content hasn't
    changed since the last sync. Notes deleted from the vault are dropped from
    the store."""
    if not VAULT_DIR.exists():
        return f"No vault found at {VAULT_DIR}. Set OBSIDIAN_VAULT to point at one."

    store = _load_store()
    # content hash per already-indexed vault note, to skip unchanged files
    indexed = {c["source"]: c.get("hash") for c in store if c["source"].startswith(VAULT_PREFIX)}

    seen, added, updated = set(), 0, 0
    for path in _vault_files():
        source = VAULT_PREFIX + path.relative_to(VAULT_DIR).as_posix()
        seen.add(source)
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if not text.strip():
            continue

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if indexed.get(source) == digest:
            continue  # unchanged since last sync

        was_indexed = source in indexed
        store = [c for c in store if c["source"] != source]
        for chunk in _chunk(text):
            emb = ollama.embeddings(model=EMBED_MODEL, prompt=chunk)["embedding"]
            store.append({"source": source, "text": chunk, "embedding": emb, "hash": digest})
        if was_indexed:
            updated += 1
        else:
            added += 1

    stale = set(indexed) - seen
    if stale:
        store = [c for c in store if c["source"] not in stale]

    _save_store(store)
    parts = []
    if added:
        parts.append(f"{added} new")
    if updated:
        parts.append(f"{updated} changed")
    if stale:
        parts.append(f"{len(stale)} removed")
    if not parts:
        return f"Vault already up to date ({len(seen)} notes)."
    return f"Synced vault: {', '.join(parts)} ({len(seen)} notes indexed)."


def list_documents() -> list[str]:
    return sorted({c["source"] for c in _load_store()})


def remove_document(filename: str) -> str:
    store = _load_store()
    kept = [c for c in store if c["source"] != filename]
    _save_store(kept)
    return f"Removed {len(store) - len(kept)} chunks for {filename}."


def search_documents(query: str, top_k: int = 4) -> str:
    store = _load_store()
    if not store:
        return "No documents uploaded yet."

    q_emb = np.array(ollama.embeddings(model=EMBED_MODEL, prompt=query)["embedding"])
    cosine_scores = []
    for c in store:
        e = np.array(c["embedding"])
        sim = float(np.dot(q_emb, e) / (np.linalg.norm(q_emb) * np.linalg.norm(e) + 1e-9))
        cosine_scores.append(sim)

    bm25 = BM25Okapi([_tokenize(c["text"]) for c in store])
    bm25_scores = bm25.get_scores(_tokenize(query))

    cosine_rank = np.argsort(np.argsort(-np.array(cosine_scores)))
    bm25_rank = np.argsort(np.argsort(-np.array(bm25_scores)))

    fused = []
    for i, c in enumerate(store):
        rrf = 1 / (RRF_K + cosine_rank[i] + 1) + 1 / (RRF_K + bm25_rank[i] + 1)
        fused.append((rrf, cosine_scores[i], bm25_scores[i], c))
    fused.sort(key=lambda x: x[0], reverse=True)

    relevant = [f for f in fused if f[1] >= MIN_SIMILARITY or f[2] > 0]
    top = relevant[:top_k]
    if not top:
        return "No sufficiently relevant chunks found."
    return "\n\n".join(
        f"[{n}] {c['source']} (score {sim:.2f})\n{c['text']}"
        for n, (_, sim, _, c) in enumerate(top, start=1)
    )
