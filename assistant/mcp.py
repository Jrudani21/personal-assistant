"""Minimal MCP (Model Context Protocol) client for the assistant.

Lets the assistant call tools exposed by external MCP servers (nanobot /
private-gpt research: MCP is the standard way to plug external tools into an
agent without hardcoding them). Servers are configured in data/config.json
under ``mcp_servers``::

    [{"name": "filesystem", "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/path"]}]

Transport is stdio with newline-delimited JSON-RPC 2.0, implemented here with
the stdlib only (no SDK dependency). Each server runs as a subprocess; tool
lists are cached per server; calls are synchronous with a timeout.

Windows has no select() on pipes, so responses are read by a background
thread into a queue (same pattern as assistant/repl.py).
"""
import json
import queue
import shutil
import subprocess
import threading

from . import config as _config

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "personal-assistant", "version": "1.0"}

_servers: dict[str, dict] = {}  # name -> {proc, q, lock, next_id, tools}


def _servers_config() -> list[dict]:
    servers = _config.get("mcp_servers", [])
    return servers if isinstance(servers, list) else []


def _resolve_command(command: str) -> str:
    """Resolve a command to a launchable path. On Windows, npm shims like
    'npx' live as npx.cmd (a batch shim), which subprocess.Popen can't find
    by bare name — so try the .cmd/.bat/.exe variants in order."""
    found = shutil.which(command)
    if found:
        return found
    for ext in (".cmd", ".bat", ".exe"):
        found = shutil.which(command + ext)
        if found:
            return found
    return command  # let Popen raise a descriptive error


def _reader(proc, q):
    for line in proc.stdout:
        q.put(line)
    q.put(None)  # stdout closed — server exited


def _start(name: str, cfg: dict) -> str:
    """Spawn the server subprocess and perform the MCP initialize handshake.
    Returns "" on success, or an error message."""
    command = cfg.get("command", "")
    args = list(cfg.get("args", []))
    if not command:
        return f"MCP server '{name}' has no command configured."

    try:
        proc = subprocess.Popen(
            [_resolve_command(command)] + args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        return f"MCP server '{name}' failed to start: {e}"

    q: queue.Queue = queue.Queue()
    threading.Thread(target=_reader, args=(proc, q), daemon=True).start()
    state = {"proc": proc, "q": q, "lock": threading.Lock(), "next_id": 0, "tools": []}
    _servers[name] = state

    err = _request(state, "initialize", {
        "protocolVersion": _PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": _CLIENT_INFO,
    }, timeout=30)
    if err:
        _stop(name)
        return f"MCP server '{name}' handshake failed: {err}"
    # protocol notification (fire and forget)
    _send(state, "notifications/initialized", None)
    return ""


def _stop(name: str) -> None:
    state = _servers.pop(name, None)
    if not state:
        return
    try:
        state["proc"].stdin.close()
    except Exception:
        pass
    try:
        state["proc"].terminate()
    except Exception:
        pass


def _send(state: dict, method: str, params) -> None:
    state["next_id"] += 1
    msg = {"jsonrpc": "2.0", "id": state["next_id"], "method": method}
    if params is not None:
        msg["params"] = params
    try:
        state["proc"].stdin.write(json.dumps(msg) + "\n")
        state["proc"].stdin.flush()
    except Exception:
        pass


def _request(state: dict, method: str, params, timeout: float = 30.0) -> str:
    """Send a request and wait for its response. Returns "" on success (the
    response is stashed on the state under '_last') or an error string."""
    with state["lock"]:
        state["next_id"] += 1
        req_id = state["next_id"]
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            state["proc"].stdin.write(json.dumps(msg) + "\n")
            state["proc"].stdin.flush()
        except Exception as e:
            return f"write failed: {e}"

        deadline = timeout
        while deadline > 0:
            try:
                line = state["q"].get(timeout=deadline)
            except queue.Empty:
                return f"timed out after {timeout:g}s"
            if line is None:
                return "server process exited"
            try:
                resp = json.loads(line)
            except Exception:
                continue  # stray non-JSON line — keep waiting
            if resp.get("id") != req_id:
                # notification or another request's response — store it for later
                state.setdefault("_extra", []).append(resp)
                continue
            if resp.get("error"):
                return resp["error"].get("message", str(resp["error"]))
            state["_last"] = resp.get("result")
            return ""
        return f"timed out after {timeout:g}s"


def _ensure(server: str) -> tuple[dict | None, str]:
    """Return (state, "") for a running server, starting it if needed."""
    if server in _servers and _servers[server]["proc"].poll() is None:
        return _servers[server], ""
    for cfg in _servers_config():
        if cfg.get("name") == server:
            err = _start(server, cfg)
            if err:
                return None, err
            return _servers.get(server), ""
    return None, f"No MCP server named '{server}' configured (see mcp_servers in config)."


def list_tools(server: str = "") -> str:
    """List MCP tools from one server (or all configured servers when
    server is empty)."""
    cfg_servers = _servers_config()
    if not cfg_servers:
        return "No MCP servers configured. Add them under 'mcp_servers' in settings."

    targets = [s for s in cfg_servers if not server or s.get("name") == server]
    if not targets:
        return f"No MCP server named '{server}' configured."

    out = []
    for cfg in targets:
        name = cfg.get("name", "?")
        state, err = _ensure(name)
        if err:
            out.append(f"{name}: {err}")
            continue
        # refresh cached tool list (cheap; servers usually return fast)
        if not state["tools"]:
            if _request(state, "tools/list", {}) == "":
                result = state.get("_last") or {}
                tools = result.get("tools", [])
                state["tools"] = [t.get("name", "?") for t in tools]
        out.append(f"{name}: {', '.join(state['tools']) or '(no tools advertised)'}")
    return "\n".join(out)


def call_tool(server: str, tool_name: str, arguments: dict) -> str:
    """Call a tool on an MCP server and return the text result."""
    state, err = _ensure(server)
    if err:
        return f"MCP error: {err}"

    timeout = float(_config.get("mcp_timeout_s", 30))
    err = _request(state, "tools/call", {
        "name": tool_name,
        "arguments": arguments or {},
    }, timeout=timeout)
    if err:
        return f"MCP call error: {err}"
    result = state.get("_last") or {}
    if result.get("isError"):
        return f"MCP tool error: {result}"
    parts = []
    for block in result.get("content", []):
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        else:
            parts.append(str(block))
    return "\n".join(parts).strip() or "(no output)"


def stop_all() -> None:
    for name in list(_servers):
        _stop(name)
