"""Standalone worker process for the persistent Python REPL tool.
Reads one JSON line per request ({"code": ...}) from stdin, execs it in a
namespace that persists across requests, writes one JSON line back with
captured stdout/stderr/error. User code's stdout is redirected to a buffer
so it can never corrupt this stdin/stdout JSON protocol.
"""
import contextlib
import io
import json
import sys
import traceback


def main():
    namespace = {}
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        request = json.loads(raw_line)
        code = request.get("code", "")

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        error = None
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                exec(compile(code, "<repl>", "exec"), namespace)
        except Exception:
            error = traceback.format_exc()

        response = {"stdout": out_buf.getvalue(), "stderr": err_buf.getvalue(), "error": error}
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
