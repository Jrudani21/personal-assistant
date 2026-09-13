"""Independent end-state verification of the second-brain graph.

Answers one question with numbers: does the graph now describe the CURRENT stack,
and is it internally healthy? Read-only -- no writes, no invalidations.

    python graph_verify_final.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

STALE = ["ollama", "qwen3-coder", "glm-4.7", "deepseek-r1", "qwen2.5", "llama3.1",
         "claude\\brain"]
CURRENT = ["lm studio", "qwen3.5", "text-embedding-nomic", "reasoning_effort", "graphiti"]

LIVE = "r.invalid_at IS NULL"


def main() -> int:
    from neo4j import GraphDatabase

    d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "graphitipass123"))
    bad = 0
    with d.session() as s:
        def one(q, **kw):
            return list(s.run(q, **kw))[0]["c"]

        print("== shape ==")
        for label, q in [
            ("episodes", "MATCH (e:Episodic) RETURN count(e) AS c"),
            ("entities", "MATCH (e:Entity) RETURN count(e) AS c"),
            ("edges live", f"MATCH ()-[r:RELATES_TO]->() WHERE {LIVE} RETURN count(r) AS c"),
            ("edges invalidated", "MATCH ()-[r:RELATES_TO]->() WHERE r.invalid_at IS NOT NULL RETURN count(r) AS c"),
        ]:
            print(f"  {label:20} {one(q)}")

        print()
        print("== health: are embeddings complete? ==")
        miss_e = one("MATCH ()-[r:RELATES_TO]->() WHERE r.fact_embedding IS NULL RETURN count(r) AS c")
        miss_n = one("MATCH (e:Entity) WHERE e.name_embedding IS NULL RETURN count(e) AS c")
        print(f"  edges missing fact_embedding  {miss_e}")
        print(f"  entities missing name_embedding {miss_n}")
        if miss_e or miss_n:
            bad += 1
            print("  !! the vector index is incomplete")

        print()
        print("== the point of the exercise ==")
        for t in STALE:
            n = one(f"MATCH ()-[r:RELATES_TO]->() WHERE {LIVE} AND toLower(r.fact) CONTAINS $t "
                    f"RETURN count(r) AS c", t=t)
            flag = "" if n <= 5 else "   <-- still high"
            print(f"  LIVE stale  {t:18} {n}{flag}")
        print()
        for t in CURRENT:
            n = one(f"MATCH ()-[r:RELATES_TO]->() WHERE {LIVE} AND toLower(r.fact) CONTAINS $t "
                    f"RETURN count(r) AS c", t=t)
            print(f"  LIVE current {t:18} {n}")

        lm = one(f"MATCH ()-[r:RELATES_TO]->() WHERE {LIVE} AND toLower(r.fact) CONTAINS 'lm studio' "
                 f"RETURN count(r) AS c")
        if lm == 0:
            bad += 1
            print("\n  !! VERDICT FAIL: the graph still says nothing about LM Studio")
        else:
            print(f"\n  VERDICT OK: {lm} live fact(s) describe the current LM Studio stack")

        print()
        print("== representative current-stack facts ==")
        for r in s.run(f"MATCH ()-[r:RELATES_TO]->() WHERE {LIVE} AND "
                       f"toLower(r.fact) CONTAINS 'lm studio' RETURN r.fact AS f LIMIT 5"):
            print("   -", (r["f"] or "")[:118])

        print()
        print("== stale facts deliberately left live (experiment/history records) ==")
        for r in s.run(f"MATCH ()-[r:RELATES_TO]->() WHERE {LIVE} AND "
                       f"(toLower(r.fact) CONTAINS 'ollama' OR toLower(r.fact) CONTAINS 'qwen3-coder') "
                       f"RETURN r.fact AS f LIMIT 6"):
            print("   -", (r["f"] or "")[:118])

    d.close()
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
