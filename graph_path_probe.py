"""Exercise the REAL graph path the assistant uses: assistant.graph.graph_search().

This is the read path server.py /api/vault/graphiti and the Graphiti MCP tool call.
It needs Neo4j (bolt) + LM Studio (embedder + reranker) together, which is exactly
the combination that was never tested while Neo4j was down.

Run from the repo root:  python graph_path_probe.py
"""
import asyncio
import time

from assistant import graph, local_llm

QUERIES = [
    "Poisson overdispersion",
    "what does the second brain use as its backend",
    "Claude Code knowledge graph",
]

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def main() -> int:
    print("--- route resolution ---")
    base = local_llm.lmstudio_base()
    chat = local_llm.chat_model()
    embed = local_llm.resolve_model(graph.EMBED_MODEL_ALIAS)
    print(f"  base_url : {base}")
    print(f"  chat     : {chat}")
    print(f"  embed    : {embed}")
    check("base_url is an OpenAI-compatible endpoint", base.rstrip('/').endswith('/v1'), base)
    check("embed alias resolved to a served model id", bool(embed) and embed != graph.EMBED_MODEL_ALIAS, embed)

    print("\n--- build_client() (the real constructor) ---")
    t0 = time.perf_counter()
    try:
        client = graph.build_client()
        check("build_client() constructed Graphiti", True, f"{time.perf_counter() - t0:.2f}s")
    except Exception as exc:  # noqa: BLE001
        check("build_client() constructed Graphiti", False, f"{type(exc).__name__}: {exc}")
        return 1

    print("\n--- build_indices_and_constraints() (idempotent, all already ONLINE) ---")

    async def _indices_and_close(c) -> None:
        # Build AND close inside ONE loop: the neo4j async driver binds to the loop
        # active on first use, so closing it from a second asyncio.run() dies with
        # "AttributeError: 'NoneType' object has no attribute 'send'".
        try:
            await c.build_indices_and_constraints()
        finally:
            await c.close()

    try:
        asyncio.run(_indices_and_close(client))
        check("indices build without error against the live DB", True)
    except Exception as exc:  # noqa: BLE001
        check("indices build without error against the live DB", False, f"{type(exc).__name__}: {exc}")

    print("\n--- graph_search() per query ---")
    for q in QUERIES:
        t0 = time.perf_counter()
        out = graph.graph_search(q, top_k=5)
        dt = time.perf_counter() - t0
        broke = out.startswith("Graph search error")
        hits = 0 if broke else sum(1 for line in out.splitlines() if line.strip().startswith("- "))
        check(f"{q!r} -> {hits} fact(s) in {dt:.2f}s", not broke and hits > 0, "" if not broke else out[:160])
        for line in out.splitlines()[:3]:
            print(f"      {line[:150]}")

    print("\n--- async variant (what the API layer awaits) ---")
    out = asyncio.run(graph.graph_search_async("Poisson overdispersion", top_k=3))
    check("graph_search_async returns facts", not out.startswith("Graph search error") and "- " in out, out[:120].replace("\n", " | "))

    print(f"\n===== {'ALL PASS' if not failures else str(len(failures)) + ' FAILURE(S): ' + ', '.join(failures)} =====")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
