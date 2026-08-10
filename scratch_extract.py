import json, os, sys, glob

BASE = r"C:\Users\Janak's PC\.claude\projects"
dirs = [
    "C--Users-Janak-s-PC-AppData-Local-Temp-claude-C--Users-Janak-s-PC-d275e211-b922-4a34-b857-e0d8b4ac30b0-scratchpad-ollama-test",
    "C--Users-Janak-s-PC-Claude",
    "C--Users-Janak-s-PC-projects-personal-assistant",
    "C--Users-Janak-s-PC",
    "C--WINDOWS-system32",
    "E--career-search",
]

def get_text(msg):
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(c.get("text",""))
                elif c.get("type") == "tool_result":
                    pass
        return " ".join(parts)
    return ""

for d in dirs:
    full = os.path.join(BASE, d)
    if not os.path.isdir(full):
        continue
    files = glob.glob(os.path.join(full, "*.jsonl"))
    print(f"\n\n#### DIR: {d}  ({len(files)} files)")
    for fp in files:
        size = os.path.getsize(fp)
        mtime = os.path.getmtime(fp)
        summaries = []
        first_user_msgs = []
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            print(f"ERR reading {fp}: {e}")
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") == "summary":
                summaries.append(obj.get("summary",""))
        if summaries:
            print(f"  FILE: {os.path.basename(fp)} size={size} mtime={mtime}")
            for s in summaries:
                print(f"    SUMMARY: {s}")
