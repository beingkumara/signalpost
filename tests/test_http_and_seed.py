import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import FakeClient
from signalpost import config, http_client, seed
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


def test_seed_samples_unique_numbers():
    kommuner = {"_embedded": {"kommuner": [{"nummer": "0301"}, {"nummer": "0999"}]}}
    units = {"page": {"totalPages": 1}, "_embedded": {"enheter": [{"organisasjonsnummer": str(100000000 + i)}
                                                                  for i in range(30)]}}
    routes = {
        config.BRREG_BASE + "/enhetsregisteret/api/kommuner?size=1000": (200, kommuner),
        f"{config.ENHET_SEARCH_URL}?kommunenummer=0301&size={seed.PAGE_SIZE}&page=0": (200, units),
    }
    picked = seed.sample_orgnrs(FakeClient(routes), 5)
    assert len(picked) == 5 and len(set(picked)) == 5
