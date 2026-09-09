"""Local RAG: chunk + embed uploaded docs via Ollama, hybrid BM25+cosine search.

Two ingest paths share one store and one search index:
- `ingest()`      — a file uploaded through the UI, stored under its filename
- `sync_vault()`  — every .md note in the Obsidian vault, stored as "vault:<rel path>"

Everything runs locally against Ollama's embedding model; nothing leaves the machine.

Tunables (chunking, similarity, model) are read from assistant/config.py so the
Settings page can adjust them without touching source.
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

from . import config as _config

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

# Nightly-learn knowledge base (deepseek-cave). The same facts get re-scraped
# every cycle under new filenames, so sync_knowledge dedups by content hash.
KNOWLEDGE_DIR = Path(os.environ.get(
    "KNOWLEDGE_DIR",
    r"C:\Users\Janak's PC\deepseek-cave\nightly-learn\knowledge",
))
KNOWLEDGE_PREFIX = "knowledge:"

_TOKEN_RE = re.compile(r"\w+")


# ---- config-backed knobs -------------------------------------------------
def _embed_model() -> str:
    return str(_config.get("rag_embed_model", EMBED_MODEL))


def _chunk_size() -> int:
    return int(_config.get("rag_chunk_size", CHUNK_SIZE))


def _chunk_overlap() -> int:
    return int(_config.get("rag_chunk_overlap", CHUNK_OVERLAP))


def _min_similarity() -> float:
    return float(_config.get("rag_min_similarity", MIN_SIMILARITY))


def _rrf_k() -> int:
    return int(_config.get("rag_rrf_k", RRF_K))


def _vault_dir() -> Path:
    cfg = _config.get("vault_dir")
    if cfg:
        return Path(cfg)
    return VAULT_DIR


def _knowledge_dir() -> Path:
    cfg = _config.get("knowledge_dir")
    if cfg:
        return Path(cfg)
    return KNOWLEDGE_DIR


def _knowledge_digest(text: str) -> str:
    """Hash knowledge content, ignoring the per-scrape 'Learned:' timestamp
    so the same fact re-scraped under a new filename dedups to one entry."""
    norm_lines = [
        ln for ln in text.splitlines()
        if not ln.strip().lstrip("- ").startswith("Learned:")
    ]
    return hashlib.sha256("\n".join(norm_lines).encode("utf-8")).hexdigest()


# ---- store ---------------------------------------------------------------
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
    chunk_size = _chunk_size()
    chunk_overlap = _chunk_overlap()
    sentences = _split_sentences(text)
    if not sentences:
        return []
    chunks = []
    current: list[str] = []
    current_len = 0
    for sent in sentences:
        if current_len + len(sent) > chunk_size and current:
            chunks.append(" ".join(current))
            overlap: list[str] = []
            overlap_len = 0
            for s in reversed(current):
                if overlap_len + len(s) > chunk_overlap:
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


# ---- ingest / vault ------------------------------------------------------
def ingest(filename: str, raw: bytes) -> str:
    text = _extract_text(filename, raw)
    if not text.strip():
        return f"No extractable text in {filename}."
    chunks = _chunk(text)
    store = [c for c in _load_store() if c["source"] != filename]  # replace prior version
    for chunk in chunks:
        emb = ollama.embeddings(model=_embed_model(), prompt=chunk)["embedding"]
        store.append({"source": filename, "text": chunk, "embedding": emb})
    _save_store(store)
    return f"Ingested {filename}: {len(chunks)} chunks."


def _vault_files() -> list[Path]:
    vdir = _vault_dir()
    if not vdir.exists():
        return []
    return [
        p for p in vdir.rglob("*.md")
        if not VAULT_SKIP_DIRS & set(p.relative_to(vdir).parts)
    ]


def sync_vault() -> str:
    """Indexes every .md note in the vault, skipping notes whose content hasn't
    changed since the last sync. Notes deleted from the vault are dropped from
    the store."""
    vdir = _vault_dir()
    if not vdir.exists():
        return f"No vault found at {vdir}. Set OBSIDIAN_VAULT (or the vault_dir setting) to point at one."

    store = _load_store()
    # content hash per already-indexed vault note, to skip unchanged files
    indexed = {c["source"]: c.get("hash") for c in store if c["source"].startswith(VAULT_PREFIX)}

    seen, added, updated = set(), 0, 0
    for path in _vault_files():
        source = VAULT_PREFIX + path.relative_to(vdir).as_posix()
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
            emb = ollama.embeddings(model=_embed_model(), prompt=chunk)["embedding"]
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


def sync_knowledge() -> str:
    """Indexes every .md file in the nightly-learn knowledge base, deduping by
    content hash so the same fact re-scraped under a new filename is only
    indexed once. Files deleted from the base are dropped from the store."""
    kdir = _knowledge_dir()
    if not kdir.exists():
        return f"No knowledge base found at {kdir}. Set KNOWLEDGE_DIR (or the knowledge_dir setting) to point at one."

    store = _load_store()
    # content hash per already-indexed knowledge file
    indexed = {c["source"]: c.get("hash") for c in store if c["source"].startswith(KNOWLEDGE_PREFIX)}
    # content hash -> first-seen source THIS run, so re-scraped duplicates
    # collapse to a single entry even on the very first sync.
    seen_content: dict[str, str] = {}
    # sources present on disk this run (for purge of deleted files)
    seen: set[str] = set()

    added, updated, skipped = 0, 0, 0
    for path in sorted(kdir.glob("*.md")):
        source = KNOWLEDGE_PREFIX + path.name
        seen.add(source)
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if not text.strip():
            continue
        digest = _knowledge_digest(text)

        # Dedup: skip any file whose content was already seen this run ?
        # the nightly-learn crew re-scrapes the same facts under new names.
        dup_source = seen_content.get(digest)
        if dup_source is not None:
            skipped += 1
            continue
        seen_content[digest] = source

        if indexed.get(source) == digest:
            continue  # unchanged since last sync

        was_indexed = source in indexed
        store = [c for c in store if c["source"] != source]
        for chunk in _chunk(text):
            emb = ollama.embeddings(model=_embed_model(), prompt=chunk)["embedding"]
            store.append({"source": source, "text": chunk, "embedding": emb, "hash": digest})
        if was_indexed:
            updated += 1
        else:
            added += 1

    # Purge entries whose knowledge .md no longer exists on disk (mirrors
    # sync_vault's stale-drop; matches this function's docstring). Only
    # knowledge:-prefixed sources — never user-uploaded docs.
    stale = set(indexed) - seen
    if stale:
        store = [c for c in store if c["source"] not in stale]

    _save_store(store)
    unique = len(seen_content)
    parts = []
    if added:
        parts.append(f"{added} new")
    if updated:
        parts.append(f"{updated} changed")
    if skipped:
        parts.append(f"{skipped} duplicate{'' if skipped == 1 else 's'} skipped")
    if stale:
        parts.append(f"{len(stale)} removed")
    if not parts:
        return f"Knowledge base already up to date ({unique} unique files)."
    return f"Synced knowledge: {', '.join(parts)} ({unique} unique files)."


def list_documents() -> list[str]:
    return sorted({c["source"] for c in _load_store()})


def remove_document(filename: str) -> str:
    store = _load_store()
    kept = [c for c in store if c["source"] != filename]
    _save_store(kept)
    return f"Removed {len(store) - len(kept)} chunks for {filename}."


def search_documents(query: str, top_k: int | None = None) -> str:
    if top_k is None:
        top_k = int(_config.get("rag_search_top_k", 4))
    store = _load_store()
    if not store:
        return "No documents uploaded yet."

    q_emb = np.array(ollama.embeddings(model=_embed_model(), prompt=query)["embedding"])
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
        rrf = 1 / (_rrf_k() + cosine_rank[i] + 1) + 1 / (_rrf_k() + bm25_rank[i] + 1)
        fused.append((rrf, cosine_scores[i], bm25_scores[i], c))
    fused.sort(key=lambda x: x[0], reverse=True)

    relevant = [f for f in fused if f[1] >= _min_similarity() or f[2] > 0]
    top = relevant[:top_k]
    if not top:
        return "No sufficiently relevant chunks found."
    return "\n\n".join(
        f"[{n}] {c['source']} (score {sim:.2f})\n{c['text']}"
        for n, (_, sim, _, c) in enumerate(top, start=1)
    )
