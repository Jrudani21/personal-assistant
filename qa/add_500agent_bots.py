#!/usr/bin/env python3
"""Add career fleet + 19 agent bots (500-AI-Agents) to data/bots.json.

Idempotent: skips bots that already exist. Preserves existing 13 bots.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOTS_FILE = ROOT / "data" / "bots.json"

data = json.loads(BOTS_FILE.read_text(encoding="utf-8"))
existing_ids = {b["id"] for b in data.get("bots", [])}
existing_fleets = {f["id"] for f in data.get("fleets", [])}

# ── New career fleet ────────────────────────────────────────────────────────
if "career" not in existing_fleets:
    data["fleets"].append({
        "id": "career",
        "name": "Career Ops",
        "coordinator": "career-lead",
        "members": [
            "career-lead", "career-parser", "career-applier", "career-outreach",
            "career-brand", "career-researcher", "career-pii",
        ],
        "notes": "Job-search pipeline: parse CV -> tailor applications -> outreach. Runs on demand.",
    })

# ── New agent bots ──────────────────────────────────────────────────────────
AGENTS = [
    # career fleet
    dict(id="career-lead", type="coordinator", name="Career Lead", fleet="career",
         schedule="manual", runner="qa/fleet_boss.py", target="career-fleet",
         model="deepseek-v4-flash", capabilities=["career-ops", "oversight"],
         budget={"max_runs_per_day": 1, "max_cost_est": 1500},
         needs_approval=False, can_approve=True, approval_levels=["routine"],
         notes="Career fleet lead: reviews pipeline outputs, links to brain, prioritizes applications."),
    dict(id="career-parser", type="agent", name="Career Parser", fleet="career",
         schedule="manual", agent_dir="09-resume-parser-agent",
         agent_args='--resume "E:/Local/projects/career-ops/cv.md"',
         model="qwen3:8b", tier="local",
         env={"DEEPSEEK_API_BASE": "http://localhost:11434/v1", "DEEPSEEK_MODEL": "qwen3:8b"},
         capabilities=["career-ops", "resume-parse"],
         budget={"max_runs_per_day": 5, "max_cost_est": 0},
         notes="Parse cv.md -> structured profile JSON + ATS fit (LOCAL tier — zero tokens; extraction is local-capable)."),
    dict(id="career-applier", type="agent", name="Career Applier", fleet="career",
         schedule="manual", agent_dir="18-job-application-agent",
         agent_args="", model="deepseek-v4-flash",
         capabilities=["career-ops", "cover-letter", "interview-prep"],
         budget={"max_runs_per_day": 10, "max_cost_est": 8000},
         verify="review", output="the drafted cover letter/application",
         notes="Tailored cover letter + resume bullets + interview Qs (FLASH tier). Pass --job-desc and --candidate. Output is flash-reviewed before use."),
    dict(id="career-outreach", type="agent", name="Career Outreach", fleet="career",
         schedule="manual", agent_dir="05-email-drafting-agent",
         agent_args="", model="deepseek-v4-flash",
         capabilities=["career-ops", "email"],
         budget={"max_runs_per_day": 10, "max_cost_est": 4000},
         notes="Draft recruiter follow-up/outreach email. Pass --context --tone --recipient."),
    dict(id="career-brand", type="agent", name="Career Brand", fleet="career",
         schedule="manual", agent_dir="14-social-media-agent",
         agent_args="", model="qwen3:8b", tier="local",
         env={"DEEPSEEK_API_BASE": "http://localhost:11434/v1", "DEEPSEEK_MODEL": "qwen3:8b"},
         capabilities=["career-ops", "social"],
         budget={"max_runs_per_day": 3, "max_cost_est": 0},
         notes="LinkedIn personal-brand posts for job search (LOCAL tier — template generation; zero tokens). Pass post topic/brand voice."),
    dict(id="career-researcher", type="agent", name="Career Researcher", fleet="career",
         schedule="manual", agent_dir="19-competitive-analysis-agent",
         agent_args='--company "Winnipeg Health Authority" --industry "healthcare"',
         model="deepseek-v4-flash", capabilities=["career-ops", "company-research"],
         budget={"max_runs_per_day": 5, "max_cost_est": 6000},
         notes="Company research before applying. Pass --company and --industry."),
    dict(id="career-pii", type="agent", name="Career PII Guard", fleet="career",
         schedule="manual", agent_dir="21-pii-sanitization-agent",
         agent_args="--local", model=None, tier="none", capabilities=["career-ops", "pii-redaction"],
         budget={"max_runs_per_day": 20, "max_cost_est": 0},
         notes="Scrub PII before any LLM call (deterministic, offline regex). Pass --text or --file."),

    # research fleet additions
    dict(id="web-researcher", type="agent", name="Web Researcher", fleet="research",
         schedule="manual", agent_dir="01-web-research-agent",
         agent_args="", model="deepseek-v4-flash", capabilities=["research", "web-search"],
         budget={"max_runs_per_day": 5, "max_cost_est": 6000},
         notes="Iterative web research report. Pass --query. (Tavily key needed for live search.)"),
    dict(id="news-digest", type="agent", name="News Digest", fleet="research",
         schedule="daily:07:00", agent_dir="06-news-summarizer-agent",
         agent_args='--topic "artificial intelligence" --count 5',
         model="qwen3:8b", tier="local",
         env={"DEEPSEEK_API_BASE": "http://localhost:11434/v1", "DEEPSEEK_MODEL": "qwen3:8b"},
         capabilities=["news-summary"],
         budget={"max_runs_per_day": 1, "max_cost_est": 0},
         notes="Daily news digest (LOCAL tier — short summarization; zero tokens). Change --topic via bot config."),
    dict(id="market-watch", type="agent", name="Market Watch", fleet="research",
         schedule="weekly:mon:07:30", agent_dir="11-stock-research-agent",
         agent_args="--ticker SPY", model="deepseek-v4-pro", tier="pro",
         capabilities=["market-research"],
         budget={"max_runs_per_day": 1, "max_cost_est": 3000},
         notes="Weekly macro gauge for weather-arb context (PRO tier — financial analysis). Pass --ticker."),
    dict(id="arb-debate", type="agent", name="Arb Debate", fleet="research",
         schedule="manual", agent_dir="20-multi-agent-debate",
         agent_args="--rounds 2", model="deepseek-v4-pro", tier="pro",
         capabilities=["hypothesis-testing"],
         budget={"max_runs_per_day": 3, "max_cost_est": 6000},
         notes="Multi-agent debate for weather-arb hypothesis testing (PRO tier — deep reasoning). Pass --topic."),

    # qa fleet additions
    dict(id="code-reviewer", type="agent", name="Code Reviewer", fleet="qa",
         schedule="manual", agent_dir="02-code-review-agent",
         agent_args="", model="deepseek-v4-flash",
         capabilities=["code-review"],
         budget={"max_runs_per_day": 10, "max_cost_est": 6000},
         notes="LLM code review. Pass --file or --code."),
    dict(id="test-writer", type="agent", name="Test Writer", fleet="qa",
         schedule="manual", agent_dir="15-unit-test-generator",
         agent_args="", model="deepseek-v4-flash",
         capabilities=["test-generation"],
         budget={"max_runs_per_day": 10, "max_cost_est": 6000},
         notes="Generate unit tests. Pass --file. Run pytest after."),
    dict(id="doc-writer", type="agent", name="Doc Writer", fleet="qa",
         schedule="manual", agent_dir="16-documentation-writer",
         agent_args="", model="deepseek-v4-flash",
         capabilities=["documentation"],
         budget={"max_runs_per_day": 10, "max_cost_est": 4000},
         notes="Docstring/README writer. Pass --file."),
    dict(id="data-analyst", type="agent", name="Data Analyst", fleet="qa",
         schedule="manual", agent_dir="08-data-analysis-agent",
         agent_args="", model="deepseek-v4-pro", tier="pro",
         capabilities=["data-analysis"],
         budget={"max_runs_per_day": 5, "max_cost_est": 6000},
         notes="CSV/Excel analysis for backtests + admin analytics (PRO tier — complex analysis). Pass --file. (Sandbox the exec.)"),

    # ops fleet additions
    dict(id="issue-triager", type="agent", name="Issue Triager", fleet="ops",
         schedule="manual", agent_dir="07-github-issue-triager",
         agent_args="", model="deepseek-v4-flash",
         capabilities=["issue-triage"],
         budget={"max_runs_per_day": 10, "max_cost_est": 4000},
         notes="Triage a GitHub issue. Pass --issue-url or --title/--body."),
    dict(id="meeting-scribe", type="agent", name="Meeting Scribe", fleet="ops",
         schedule="manual", agent_dir="10-meeting-notes-agent",
         agent_args="", model="qwen3:8b", tier="local",
         env={"DEEPSEEK_API_BASE": "http://localhost:11434/v1", "DEEPSEEK_MODEL": "qwen3:8b"},
         capabilities=["meeting-notes"],
         budget={"max_runs_per_day": 5, "max_cost_est": 0},
         notes="Turn a transcript into notes + action items (LOCAL tier — extraction; zero tokens). Pass transcript text."),
    dict(id="sql-agent", type="agent", name="SQL Agent", fleet="ops",
         schedule="manual", agent_dir="04-sql-query-agent",
         agent_args="", model="deepseek-v4-flash",
         capabilities=["sql"],
         budget={"max_runs_per_day": 10, "max_cost_est": 4000},
         notes="Natural-language -> SQL. Pass --db and --query."),
    dict(id="kb-agent", type="agent", name="KB Agent", fleet="ops",
         schedule="manual", agent_dir="13-customer-support-agent",
         agent_args='--query "help"', model="deepseek-v4-flash",
         capabilities=["knowledge-base"],
         budget={"max_runs_per_day": 20, "max_cost_est": 4000},
         notes="Internal KB Q&A. Pass --kb-dir and --query."),
    dict(id="doc-qa", type="agent", name="Doc QA", fleet="ops",
         schedule="manual", agent_dir="03-pdf-qa-agent",
         agent_args="", model="deepseek-v4-flash",
         capabilities=["document-qa"],
         budget={"max_runs_per_day": 10, "max_cost_est": 4000},
         notes="Ask questions over a PDF. Pass --pdf and --question."),
    dict(id="brain-qa", type="agent", name="Brain QA", fleet="ops",
         schedule="manual", agent_dir="22-vault-rag-agent",
         agent_args="", model="deepseek-v4-flash", tier="flash",
         capabilities=["brain-vault", "rag"],
         budget={"max_runs_per_day": 20, "max_cost_est": 4000},
         notes="Ask your brain vault (E:/Local/brain): RAG over md files via MiniLM+FAISS. Flash by default; add --local for zero-token answers."),
]

# Add research-fleet additions to the research members list
research_fleet = next(f for f in data["fleets"] if f["id"] == "research")
for mid in ["web-researcher", "news-digest", "market-watch", "arb-debate"]:
    if mid not in research_fleet["members"]:
        research_fleet["members"].append(mid)

# Add qa-fleet additions
qa_fleet = next(f for f in data["fleets"] if f["id"] == "qa")
for mid in ["code-reviewer", "test-writer", "doc-writer", "data-analyst"]:
    if mid not in qa_fleet["members"]:
        qa_fleet["members"].append(mid)

# Add ops-fleet additions
ops_fleet = next(f for f in data["fleets"] if f["id"] == "ops")
for mid in ["issue-triager", "meeting-scribe", "sql-agent", "kb-agent", "doc-qa"]:
    if mid not in ops_fleet["members"]:
        ops_fleet["members"].append(mid)

added = 0
for bot in AGENTS:
    if bot["id"] not in existing_ids:
        data["bots"].append(bot)
        added += 1
    else:
        # Update in place for idempotent re-runs
        for i, b in enumerate(data["bots"]):
            if b["id"] == bot["id"]:
                data["bots"][i] = bot
                break

BOTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Added {added} new bots. Total bots: {len(data['bots'])}, fleets: {len(data['fleets'])}")
print("Fleets:", [f["id"] for f in data["fleets"]])
