"""Independent end-state verification of the second-brain graph.

Read-only: no writes, no invalidations, no model, no GPU.

    python graph_verify_final.py

Two questions, deliberately separated:

  1. HEALTH   - does the graph describe the CURRENT stack, and is it internally sound?
                This is the pass/fail signal.
  2. CANDIDATES - live facts that merely MENTION dead infrastructure. These are NOT
                failures. A fact can name a dead tool and still be perfectly true
                ("LM Studio replaced Ollama...", "Ollama processes are invisible to
                nvidia-smi under WDDM"), so this list is input to the model judge in
                graph_refresh.invalidate(), not a verdict. Reporting it as an alarm was
                the old bug here: it cried wolf about a correct graph.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

VAULT = Path(r"E:\Local\brain")
NOTE_DIRS = [VAULT / "notes", VAULT / "projects"]

# Markers used to FIND candidates. Recall only -- never a verdict.
CANDIDATE_MARKERS = ["ollama", "qwen3-coder", "glm-4.7", "deepseek-r1", "qwen2.5",
                     "llama3.1", "claude\\brain"]
# Facts asserting the CURRENT stack. At least one must be live, or the graph is silent
# about what the machine actually runs today.
CURRENT_MARKERS = ["lm studio", "qwen3.5", "text-embedding-nomic", "graphiti"]

LIVE = "r.invalid_at IS NULL"


def main() -> int:
    from neo4j import GraphDatabase

    d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "graphitipass123"))
    failures: list[str] = []

    with d.session() as s:
        def one(q, **kw) -> int:
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
        print("== health: embeddings ==")
        miss_e = one("MATCH ()-[r:RELATES_TO]->() WHERE r.fact_embedding IS NULL RETURN count(r) AS c")
        miss_n = one("MATCH (e:Entity) WHERE e.name_embedding IS NULL RETURN count(e) AS c")
        print(f"  edges missing fact_embedding    {miss_e}")
        print(f"  entities missing name_embedding {miss_n}")
        if miss_e or miss_n:
            failures.append("vector index incomplete")

        print()
        print("== health: does the graph know the CURRENT stack? ==")
        current_total = 0
        for t in CURRENT_MARKERS:
            n = one(f"MATCH ()-[r:RELATES_TO]->() WHERE {LIVE} AND toLower(r.fact) CONTAINS $t "
                    f"RETURN count(r) AS c", t=t)
            current_total += n
            print(f"  LIVE current  {t:20} {n}")
        if current_total == 0:
            failures.append("graph asserts nothing about the current stack")

        print()
        print("== health: which notes are NOT in the graph? ==")
        notes = sorted(p for dd in NOTE_DIRS if dd.exists()
                       for p in dd.rglob("*.md") if not p.name.startswith("."))
        missing = []
        for p in notes:
            n = one("MATCH (e:Episodic) WHERE e.name = $n RETURN count(e) AS c", n=p.stem)
            if not n:
                missing.append(p)
        print(f"  {len(notes) - len(missing)}/{len(notes)} notes have an episode")
        for p in missing:
            print(f"    MISSING  {p.relative_to(VAULT)}")
        if missing:
            failures.append(f"{len(missing)} note(s) not ingested")

        print()
        print("== candidates for the model judge (NOT failures) ==")
        print("  Live facts that MENTION dead infrastructure. Each may be a stale assertion")
        print("  OR a true statement about the migration. graph_refresh.invalidate() decides.")
        cand = one(f"MATCH ()-[r:RELATES_TO]->() WHERE {LIVE} AND "
                   f"any(m IN $markers WHERE toLower(r.fact) CONTAINS m) RETURN count(r) AS c",
                   markers=CANDIDATE_MARKERS)
        print(f"  candidate facts: {cand}")
        for r in s.run(f"MATCH ()-[r:RELATES_TO]->() WHERE {LIVE} AND "
                       f"any(m IN $markers WHERE toLower(r.fact) CONTAINS m) "
                       f"RETURN r.fact AS f LIMIT 5", markers=CANDIDATE_MARKERS):
            print("    .", (r["f"] or "")[:108])

        print()
        print("== representative current-stack facts ==")
        for r in s.run(f"MATCH ()-[r:RELATES_TO]->() WHERE {LIVE} AND "
                       f"toLower(r.fact) CONTAINS 'lm studio' RETURN r.fact AS f LIMIT 4"):
            print("   -", (r["f"] or "")[:112])

    d.close()

    print()
    if failures:
        print("VERDICT: PROBLEMS")
        for f in failures:
            print(f"  !! {f}")
        return 1
    print("VERDICT: OK - current stack asserted, embeddings complete, all notes ingested")
    print(f"  ({cand} candidate fact(s) await the model judge; run graph_refresh.py with "
          f"GRAPH_REFRESH_INVALIDATE_DRY=1 to see the verdicts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
