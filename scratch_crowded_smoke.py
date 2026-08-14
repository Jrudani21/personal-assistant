"""Crowded-prompt smoke test re-run against the CURRENT Ollama stack.

Replicates the methodology from brain/notes "Local models invert numeric
comparisons under prompt crowding": figures already computed and stated in the
prompt, buried among unrelated statistics prose, ask for 3 takeaways.

Models under test (Aug 14 stack):
  - deepseek-r1:7b   (router default — every shape routes here)
  - deepseek-r1:14b  (reasoning tier)
  - qwen3:8b         (news bot model)

3 runs each, raw output saved for MANUAL grading (regex grading unreliable —
see note). Uses Ollama /api/generate directly; never touches the Hermes
proxy/fallback chain.
"""
import json
import time
import urllib.request

OLLAMA = "http://localhost:11434"
MODELS = ["deepseek-r1:7b", "deepseek-r1:14b", "qwen3:8b"]
RUNS = 3
OUT = r"E:\Local\projects\personal-assistant\data\crowded_smoke_20260814.jsonl"

# --- The crowded prompt: verified figures + 4 unrelated stats facts -------
CROWDED = (
    "Verified figures from the Quant stage:\n"
    "- Product A revenue per unit: $30\n"
    "- Product B revenue per unit: $50\n"
    "- Product C revenue per unit: $25\n"
    "\n"
    "Context: the Negative Binomial distribution adds a dispersion parameter "
    "to handle overdispersion in Poisson count models. SARIMA handles "
    "seasonality through a second differencing pass. The bootstrap resamples "
    "with replacement to estimate standard errors without parametric "
    "assumptions. Bayesian credible intervals are not the same as frequentist "
    "confidence intervals.\n"
    "\n"
    "Identify the 3 most important takeaways from the fetched notes and "
    "computed figures."
)


def run_once(model: str, prompt: str) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 800},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.loads(r.read())
    msg = data.get("message", {})
    return msg.get("content", ""), round(time.time() - t0, 1)


def main():
    with open(OUT, "w", encoding="utf-8") as f:
        for model in MODELS:
            print(f"=== {model} ===", flush=True)
            for i in range(1, RUNS + 1):
                try:
                    resp, secs = run_once(model, CROWDED)
                except Exception as e:
                    rec = {"model": model, "run": i, "error": str(e)}
                    print(f"  run {i}: ERROR {e}", flush=True)
                else:
                    rec = {"model": model, "run": i, "seconds": secs, "output": resp}
                    print(f"  run {i}: {secs}s, {len(resp)} chars", flush=True)
                f.write(json.dumps(rec) + "\n")
                f.flush()
    print(f"\nSaved: {OUT}")


if __name__ == "__main__":
    main()
