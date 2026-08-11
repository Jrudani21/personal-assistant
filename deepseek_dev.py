"""Hand a spec to DeepSeek and get the implementation back.

Workflow: Claude (or you) writes the spec; DeepSeek writes the code. Mirrors
what KEN_BUILD_SPEC.md / KEN_ADDENDUM_deepseek_api.md already do by hand,
but without the copy-paste.

    # implement a spec, writing files into the repo
    py -3.12 deepseek_dev.py build SPEC.md --out server.py

    # design / debugging analysis (reasoner, slower, shows its thinking)
    py -3.12 deepseek_dev.py ask "why does X fail on iOS?" -f ken.html

    # review files against a question
    py -3.12 deepseek_dev.py review "any races here?" -f server.py -f app.py

Key: DEEPSEEK_API_KEY env var, else ~/.deepseek_key.
Models: deepseek-chat (fast, bulk code) / deepseek-reasoner (slow, analysis).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
CHAT_MODEL = "deepseek-chat"
REASON_MODEL = "deepseek-reasoner"


def _client() -> OpenAI:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        try:
            key = (Path.home() / ".deepseek_key").read_text(encoding="utf-8").strip()
        except Exception:
            pass
    if not key:
        sys.exit("No DeepSeek key: set DEEPSEEK_API_KEY or create ~/.deepseek_key")
    return OpenAI(api_key=key, base_url="https://api.deepseek.com")


def _attach(paths: list[str]) -> str:
    """Inline the referenced files so the model sees real code, not a summary."""
    out = []
    for p in paths or []:
        f = Path(p)
        if not f.is_absolute():
            f = BASE_DIR / p
        if not f.exists():
            sys.exit(f"No such file: {f}")
        lang = {".py": "python", ".html": "html", ".ps1": "powershell",
                ".json": "json", ".md": "markdown"}.get(f.suffix, "")
        out.append(f"\n### {f.name}\n```{lang}\n{f.read_text(encoding='utf-8')}\n```")
    return "".join(out)


def _stream(client: OpenAI, model: str, prompt: str, system: str | None = None) -> str:
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    resp = client.chat.completions.create(model=model, messages=messages, stream=True)
    chunks: list[str] = []
    for chunk in resp:
        d = chunk.choices[0].delta
        # reasoner exposes its chain-of-thought separately; show it on stderr
        # so stdout stays clean enough to redirect into a file.
        think = getattr(d, "reasoning_content", None)
        if think:
            print(think, end="", flush=True, file=sys.stderr)
        if d.content:
            chunks.append(d.content)
            print(d.content, end="", flush=True)
    print()
    return "".join(chunks)


def _extract_code(text: str) -> str | None:
    """Pull the first fenced block out of a reply, for --out."""
    if "```" not in text:
        return None
    body = text.split("```", 1)[1]
    if "\n" in body:                       # drop the language tag line
        body = body.split("\n", 1)[1]
    return body.rsplit("```", 1)[0] if "```" in body else body


def cmd_build(args) -> None:
    spec_path = Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = BASE_DIR / args.spec
    if not spec_path.exists():
        sys.exit(f"No such spec: {spec_path}")
    prompt = (
        f"{spec_path.read_text(encoding='utf-8')}\n"
        f"{_attach(args.file)}\n\n"
        "Output ONLY the finished file in a single fenced code block, no commentary "
        "before or after. Follow the spec exactly; do not invent module or symbol "
        "names — if the spec references existing code, use the names shown above."
    )
    text = _stream(_client(), args.model, prompt,
                   system="You are a senior engineer. Produce complete, runnable "
                          "files. No placeholders, no TODOs, no elisions.")
    if args.out:
        code = _extract_code(text)
        if not code:
            sys.exit("\nNo fenced code block in the reply; nothing written.")
        dest = Path(args.out) if Path(args.out).is_absolute() else BASE_DIR / args.out
        if dest.exists() and not args.force:
            sys.exit(f"\n{dest} exists. Pass --force to overwrite.")
        dest.write_text(code, encoding="utf-8")
        print(f"\n-> wrote {dest} ({len(code)} chars)", file=sys.stderr)


def cmd_ask(args) -> None:
    _stream(_client(), args.model, f"{args.question}\n{_attach(args.file)}")


def cmd_review(args) -> None:
    prompt = (
        f"Review the following for correctness and robustness.\n\n"
        f"Focus: {args.question}\n{_attach(args.file)}\n\n"
        "Be specific and terse. Quote the exact offending lines, explain the "
        "failure, give the minimal fix. Rank findings by real-world impact. "
        "Skip generic best-practice advice."
    )
    _stream(_client(), args.model, prompt)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="implement a spec file")
    b.add_argument("spec")
    b.add_argument("-f", "--file", action="append", help="existing file to show the model")
    b.add_argument("--out", help="write the returned code block here")
    b.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    b.add_argument("--model", default=CHAT_MODEL)
    b.set_defaults(fn=cmd_build)

    a = sub.add_parser("ask", help="analysis / debugging (reasoner)")
    a.add_argument("question")
    a.add_argument("-f", "--file", action="append")
    a.add_argument("--model", default=REASON_MODEL)
    a.set_defaults(fn=cmd_ask)

    r = sub.add_parser("review", help="review files against a question")
    r.add_argument("question")
    r.add_argument("-f", "--file", action="append")
    r.add_argument("--model", default=REASON_MODEL)
    r.set_defaults(fn=cmd_review)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
