from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from signalpost.http_client import HttpClient, Response

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeClient(HttpClient):
    """Serves canned responses by URL; unknown URLs return 404. Counts the budget like the real client."""

    def __init__(self, routes: dict[str, tuple[int, Any]], budget: int = 1000,
                 content_types: dict[str, str] | None = None, fail: set[str] | None = None) -> None:
        super().__init__(budget)
        self.routes = routes
        self.content_types = content_types or {}
        self.fail = fail or set()
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> Response:
        self._reserve()
        self.calls.append(url)
        if url in self.fail:
            raise ConnectionError(f"simulated failure for {url}")
        status, body = self.routes.get(url, (404, b""))
        if not isinstance(body, (bytes, str)):
            body = json.dumps(body)
        raw = body.encode("utf-8") if isinstance(body, str) else body
        headers = {"content-type": self.content_types.get(url, "application/json")}
        return Response(url, url, status, raw, headers, "2026-09-29T10:00:00Z")


EQUINOR = "923609016"


@pytest.fixture
def equinor_routes() -> dict[str, tuple[int, Any]]:
    base = "https://data.brreg.no"
    return {
        f"{base}/enhetsregisteret/api/enheter/{EQUINOR}": (200, load_fixture("enhet_923609016.json")),
        f"{base}/enhetsregisteret/api/enheter/{EQUINOR}/roller": (200, load_fixture("roller_923609016.json")),
        f"{base}/regnskapsregisteret/regnskap/{EQUINOR}": (200, load_fixture("regnskap_923609016.json")),
        f"{base}/enhetsregisteret/api/underenheter?overordnetEnhet={EQUINOR}&size=5":
            (200, load_fixture("underenheter_923609016.json")),
    }
