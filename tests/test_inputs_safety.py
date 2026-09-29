import gzip
import json

import pytest

from signalpost import config
from signalpost.http_client import HttpClient
from signalpost.inputs import read_batch, unique_in_order
from signalpost.safe_url import UnsafeUrl, check_url


@pytest.mark.parametrize("content,suffix", [
    ('{"organisation_number":"923609016"}\n{"organisation_number":"935567645"}\n', ".jsonl"),
    ('["923609016", {"orgnr": "935567645"}]', ".json"),
    ("organisation_number,name\n923609016,A\n935567645,B\n", ".csv"),
    ("# comment\n923609016\n935567645\n", ".txt"),
])
def test_read_batch_formats(tmp_path, content, suffix):
    path = tmp_path / f"b{suffix}"
    path.write_text(content, encoding="utf-8")
    assert read_batch(path) == ["923609016", "935567645"]


def test_read_batch_gzip(tmp_path):
    path = tmp_path / "b.jsonl.gz"
    path.write_bytes(gzip.compress(json.dumps({"organisation_number": "923609016"}).encode()))
    assert read_batch(path) == ["923609016"]


def test_unique_in_order():
    assert unique_in_order(["923609016", "923 609 016", "1"]) == (["923609016", "1"], ["923 609 016"])


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/", "http://10.0.0.5/x",
                                 "http://169.254.169.254/latest/meta-data", "http://[::1]/", "https://a.b:8443/",
                                 "http://user:pw@example.com/", "http://intranet/", "ftp://example.com/"])
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeUrl):
        check_url(url, resolve=False)


def test_public_ip_allowed():
    assert check_url("https://8.8.8.8/", resolve=False)


def test_client_refuses_unsafe_untrusted_url():
    with pytest.raises(ConnectionError, match="unsafe"):
        HttpClient(5).get("http://127.0.0.1/", untrusted=True)


def test_host_cooldown_returns_synthetic_429_without_spending_budget(monkeypatch):
    client = HttpClient(5)
    monkeypatch.setitem(config.HOST_MIN_INTERVAL_SECONDS, "example.test", 0)
    client._note_status("https://example.test/x", 429)
    monkeypatch.setitem(config.HOST_COOLDOWN_SECONDS, "example.test", 60)
    client._note_status("https://example.test/x", 429)
    resp = client.get("https://example.test/y")
    assert resp.status == 429 and client.used == 0
