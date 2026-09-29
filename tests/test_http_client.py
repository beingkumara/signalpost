import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from signalpost import config
from signalpost.http_client import BudgetExceeded, HttpClient


class _Handler(BaseHTTPRequestHandler):
    hits = {"flaky": 0}

    def do_GET(self):  # noqa: N802
        if self.path == "/flaky":
            _Handler.hits["flaky"] += 1
            if _Handler.hits["flaky"] == 1:
                self.send_response(503)
                self.end_headers()
                return
        status = 404 if self.path == "/missing" else 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_get_json_and_404(server, monkeypatch):
    client = HttpClient(budget=10)
    assert client.get(server + "/ok").json() == {"ok": True}
    assert client.get(server + "/missing").status == 404
    assert client.used == 2


def test_retry_on_503_counts_budget(server, monkeypatch):
    monkeypatch.setattr(config, "RETRY_BACKOFF_SECONDS", 0)
    client = HttpClient(budget=10)
    resp = client.get(server + "/flaky")
    assert resp.status == 200 and client.used == 2


def test_budget_is_enforced(server):
    client = HttpClient(budget=1)
    client.get(server + "/ok")
    with pytest.raises(BudgetExceeded):
        client.get(server + "/ok")


def test_unreachable_host_raises_connection_error(monkeypatch):
    monkeypatch.setattr(config, "RETRY_BACKOFF_SECONDS", 0)
    with pytest.raises(ConnectionError):
        HttpClient(budget=5).get("http://127.0.0.1:9/", timeout=0.5, retries=1)




def test_normalise_url_encodes_spaces_and_idna():
    from signalpost.http_client import normalise_url
    assert normalise_url("https://acme.no/blog/Post Title One") == "https://acme.no/blog/Post%20Title%20One"
    assert normalise_url("https://bjørn.no/å?q=ø") == "https://xn--bjrn-hra.no/%C3%A5?q=%C3%B8"
    assert normalise_url("https://acme.no/a%20b") == "https://acme.no/a%20b"


def test_module_crash_is_isolated(equinor_routes, monkeypatch):
    import time
    from conftest import EQUINOR, FakeClient
    from signalpost import agent

    def boom(*a, **k):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(agent, "fetch_roles", boom)
    ctx = agent.RunContext(FakeClient(equinor_routes), time.monotonic() + 60, 1, 0, False, False)
    profile = agent.research(ctx, EQUINOR)
    states = {m["module"]: m["state"] for m in profile["modules"]}
    assert profile["status"] == "active" and states["roles"] == "failed" and states["annual_accounts"] == "available"
