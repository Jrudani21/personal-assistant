"""Prove the repointed local tier serves tokens, and the fallback detector is honest.

Run: python verify_local_tier.py
"""
import sys

sys.path.insert(0, r"E:\Local\projects\personal-assistant")

from dotenv import load_dotenv

# Mirror the app: keys come from .env, so the "healthy" direction below is real.
load_dotenv(r"E:\Local\projects\personal-assistant\.env", override=False)

from assistant import crew, orchestra  # noqa: E402

print("=" * 70)
print("1) the local pointer")
print("   OL_URL      :", orchestra.OL_URL)
print("   LOCAL_MODEL :", orchestra.LOCAL_MODEL)
assert "11434" not in orchestra.OL_URL, "FAIL: still points at dead Ollama"
assert "127.0.0.1:1234" in orchestra.OL_URL, "FAIL: not the LM Studio port"
assert orchestra.OL_URL.endswith("/v1"), "FAIL: missing /v1 (the OpenAI path needs it)"
print("   -> points at LM Studio, with the required /v1  OK")

print()
print("2) every chain tail is now the local model")
for role, chain in orchestra.CHAINS.items():
    ok, model, url = chain[-1]
    assert ok is True and model == orchestra.LOCAL_MODEL and url == orchestra.OL_URL, role
    print(f"   {role:9s} tail: {model} @ {url}  OK")

print()
print("3) forced-keyless: force every cloud condition False, then resolve()")
keyless = {
    role: [(o if o is True else False, m, u) for (o, m, u) in chain]
    for role, chain in orchestra.CHAINS.items()
}
orchestra.CHAINS = keyless
llm = orchestra.resolve("analyst")
print("   resolved model   :", llm.model)
print("   resolved base_url:", llm.base_url)
assert str(llm.base_url).startswith(orchestra.OL_URL), "FAIL: not routed to LM Studio"
assert llm.model == orchestra.LOCAL_MODEL, f"FAIL: wrong model resolved: {llm.model}"
# (Not asserting the "openai/" prefix: CrewAI strips the provider prefix off the
#  model string it stores, so that only tested CrewAI internals, not our contract.)
print("   -> routed to the local model  OK")

print()
print("4) real inference through that resolved LLM (no mocking)")
reply = llm.call("Reply with exactly the single word: pong /no_think")
text = str(reply).strip()
print("   reply:", repr(text[:200]))
assert "pong" in text.lower(), f"FAIL: local model did not answer: {text[:200]!r}"
print("   -> LM Studio actually served tokens  OK")

print()
print("5) detector: keyless crew must be flagged as local-fallback")
c_local = crew.build_crew("Say hello")
detected = crew._used_local_fallback(c_local)
print("   _used_local_fallback(keyless crew) =", detected)
assert detected is True, "FAIL: keyless run not flagged -> would be cached"
print("   -> would NOT be cached (rule fires)  OK")

print()
print("6) detector: with keys loaded, a cloud-resolved crew must NOT be flagged")
orchestra.CHAINS = {  # restore the real chains (keys came from .env)
    role: list(chain)
    for role, chain in {
        "fetcher": [(orchestra.DS_KEY, "deepseek-chat", orchestra.DS_URL),
                    (orchestra.GM_KEY, "gemini-3-flash-preview", orchestra.GM_URL),
                    (True, orchestra.LOCAL_MODEL, orchestra.OL_URL)],
        "quant": [(orchestra.DS_KEY, "deepseek-chat", orchestra.DS_URL),
                  (orchestra.GM_KEY, "gemini-3-flash-preview", orchestra.GM_URL),
                  (True, orchestra.LOCAL_MODEL, orchestra.OL_URL)],
        "analyst": [(orchestra.GM_KEY, "gemini-3-flash-preview", orchestra.GM_URL),
                    (orchestra.DS_KEY, "deepseek-chat", orchestra.DS_URL),
                    (True, orchestra.LOCAL_MODEL, orchestra.OL_URL)],
        "reporter": [(orchestra.DS_KEY, "deepseek-chat", orchestra.DS_URL),
                     (orchestra.GM_KEY, "gemini-3-flash-preview", orchestra.GM_URL),
                     (True, orchestra.LOCAL_MODEL, orchestra.OL_URL)],
    }.items()
}
print("   DeepSeek key present:", bool(orchestra.DS_KEY),
      "| Gemini key present:", bool(orchestra.GM_KEY))
c_cloud = crew.build_crew("Say hello")
cloud_detected = crew._used_local_fallback(c_cloud)
print("   _used_local_fallback(cloud crew) =", cloud_detected)
if orchestra.DS_KEY or orchestra.GM_KEY:
    assert cloud_detected is False, "FAIL: healthy run wrongly flagged -> cache never fills"
    print("   -> cached normally (no false alarm)  OK")
else:
    print("   (no keys in this shell -> both runs local; direction not provable here)")

print()
print("=" * 70)
print("ALL CHECKS PASSED")
