"""fetch_webpage target validation. Fully offline: DNS and the HTTP layer are
faked, and any real socket connect fails the test."""
import ipaddress
import socket

import pytest

from assistant import tools

PUBLIC_IP = "93.184.216.34"


class FakeResp:
    def __init__(self, status=200, text="<p>hello public</p>", location=None):
        self.status_code = status
        self.text = text
        self.headers = {"Location": location} if location else {}


@pytest.fixture
def net(monkeypatch):
    """Returns (dns, responses, calls). dns maps hostname -> [ip, ...];
    IP literals resolve to themselves; anything else is NXDOMAIN."""
    dns: dict[str, list[str]] = {"public.example": [PUBLIC_IP]}
    responses: dict[str, FakeResp] = {}
    calls: list[str] = []

    def fake_getaddrinfo(host, port, *a, **k):
        try:
            ips = [str(ipaddress.ip_address(host))]
        except ValueError:
            if host not in dns:
                raise socket.gaierror(f"nx {host}")
            ips = dns[host]
        out = []
        for ip in ips:
            if ":" in ip:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port or 0, 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0)))
        return out

    def no_connect(*a, **k):
        raise AssertionError("test attempted a real network connection")

    def fake_get(url, **kw):
        calls.append(url)
        return responses.get(url, FakeResp())

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", no_connect)
    monkeypatch.setattr(socket.socket, "connect", no_connect)
    monkeypatch.setattr(tools.requests, "get", fake_get)
    return dns, responses, calls


def _refused(result: str) -> bool:
    return result.startswith("Fetch error: refusing")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1234/v1/models",           # loopback
    "http://localhost:8756/api/health",          # loopback by name
    "http://10.0.0.5/",                          # RFC1918
    "http://192.168.1.10/",                      # RFC1918
    "http://172.16.0.1/",                        # RFC1918
    "http://169.254.169.254/latest/meta-data/",  # link-local / cloud metadata
    "http://100.100.100.100/",                   # CGNAT (tailnet)
    "http://0.0.0.0:8756/",                      # unspecified
    "http://[::1]:8756/",                        # IPv6 loopback
    "http://[fd00::1]/",                         # IPv6 ULA
    "http://[fe80::1]/",                         # IPv6 link-local
    "http://[::ffff:127.0.0.1]/",                # IPv4-mapped IPv6
    "http://[::ffff:a9fe:a9fe]/",                # IPv4-mapped metadata
    "http://[64:ff9b::7f00:1]/",                 # NAT64-embedded loopback
    "http://224.0.0.1/",                         # multicast
    "http://2130706433/",                        # decimal 127.0.0.1
    "http://0x7f000001/",                        # hex 127.0.0.1
    "http://0177.0.0.1/",                        # octal 127.0.0.1
])
def test_private_targets_are_refused(net, url):
    dns, _, calls = net
    # What the OS resolver does with numeric shorthands.
    dns.update({"localhost": ["127.0.0.1", "::1"], "2130706433": ["127.0.0.1"],
                "0x7f000001": ["127.0.0.1"], "0177.0.0.1": ["127.0.0.1"]})
    result = tools.fetch_webpage(url)
    assert _refused(result), result
    assert calls == []


def test_hostname_resolving_to_private_ip_is_refused(net):
    dns, _, calls = net
    dns["rebind.example"] = [PUBLIC_IP, "127.0.0.1"]  # any private record is enough
    assert _refused(tools.fetch_webpage("http://rebind.example/"))
    assert calls == []


def test_redirect_to_private_is_refused(net):
    _, responses, calls = net
    responses["http://public.example/"] = FakeResp(302, location="http://127.0.0.1:1234/v1/models")
    result = tools.fetch_webpage("http://public.example/")
    assert _refused(result), result
    assert calls == ["http://public.example/"]


def test_redirect_loop_is_capped(net):
    _, responses, calls = net
    responses["http://public.example/a"] = FakeResp(302, location="/a")
    result = tools.fetch_webpage("http://public.example/a")
    assert result.startswith("Fetch error") and "redirects" in result
    assert len(calls) == 6


def test_public_redirect_is_followed(net):
    _, responses, calls = net
    responses["https://public.example/old"] = FakeResp(301, location="/new")
    responses["https://public.example/new"] = FakeResp(200, "<p>moved here</p>")
    assert tools.fetch_webpage("https://public.example/old") == "moved here"
    assert calls == ["https://public.example/old", "https://public.example/new"]


@pytest.mark.parametrize("url", [
    "http://user:pw@public.example/",
    "http://public.example@127.0.0.1/",
    "file:///C:/Windows/win.ini",
    "ftp://public.example/",
    "gopher://public.example/",
    "http://public.example\\@127.0.0.1/",
    "http://nx.example/",
])
def test_malformed_or_non_http_urls_are_refused(net, url):
    _, _, calls = net
    assert _refused(tools.fetch_webpage(url))
    assert calls == []


def test_public_host_is_fetched(net):
    _, _, calls = net
    assert tools.fetch_webpage("https://public.example/page") == "hello public"
    assert calls == ["https://public.example/page"]


def test_connect_time_check_pins_to_validated_address(net, monkeypatch):
    """The socket layer re-resolves and connects only to a validated IP, so a
    DNS answer that changes between check and connect cannot reach a private
    address."""
    dns, _, _ = net
    seen = []
    monkeypatch.setattr(tools, "_ORIG_CREATE_CONNECTION", lambda addr, *a, **k: seen.append(addr) or "sock")
    tools._guard.active = True
    try:
        dns["flip.example"] = ["127.0.0.1"]
        with pytest.raises(ValueError):
            tools._guarded_create_connection(("flip.example", 80))
        dns["flip.example"] = [PUBLIC_IP]
        assert tools._guarded_create_connection(("flip.example", 80)) == "sock"
    finally:
        tools._guard.active = False
    assert seen == [(PUBLIC_IP, 80)]
    # Outside fetch_webpage the wrapper is a pass-through.
    assert tools._guarded_create_connection(("127.0.0.1", 1234)) == "sock"


def test_guard_is_wired_into_urllib3(monkeypatch):
    """Real requests/urllib3 stack (no fakes above the socket): the connect
    hook refuses before any socket is opened."""
    import requests

    def no_connect(*a, **k):
        raise AssertionError("test attempted a real network connection")

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    tools._guard.active = True
    try:
        with pytest.raises(ValueError, match="non-public"):
            requests.get("http://127.0.0.1:9/", timeout=2, proxies={"http": "", "https": ""})
    finally:
        tools._guard.active = False
