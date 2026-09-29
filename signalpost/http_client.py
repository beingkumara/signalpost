"""Small stdlib HTTP client with a hard outbound request budget."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from signalpost import config


class BudgetExceeded(RuntimeError):
    """Raised before a request would exceed the configured outbound budget."""


@dataclass(frozen=True)
class Response:
    url: str
    final_url: str
    status: int
    body: bytes
    headers: dict[str, str]
    fetched_at: str

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("content-type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        return self.body.decode(charset, errors="replace")


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class HttpClient:
    """Thread-safe client. Every network attempt counts against the budget."""

    def __init__(self, budget: int = config.DEFAULT_REQUEST_BUDGET) -> None:
        self._budget = budget
        self._used = 0
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self._budget - self._used)

    def _reserve(self) -> None:
        with self._lock:
            if self._used >= self._budget:
                raise BudgetExceeded(f"request budget of {self._budget} exhausted")
            self._used += 1

    def get(
        self,
        url: str,
        *,
        accept: str = "application/json",
        timeout: float = config.HTTP_TIMEOUT_SECONDS,
        max_bytes: int | None = None,
        retries: int = config.HTTP_RETRIES,
    ) -> Response:
        """GET with retries on 429/5xx/network errors. 4xx responses are returned, not raised."""
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            self._reserve()
            try:
                return self._fetch_once(url, accept, timeout, max_bytes)
            except urllib.error.HTTPError as err:
                if err.code == 429 or err.code >= 500:
                    last_error = err
                else:
                    body = err.read() if err.fp else b""
                    return Response(url, url, err.code, body, _lower(err.headers), utc_now_iso())
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
                last_error = err
            if attempt < retries:
                time.sleep(config.RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise ConnectionError(f"GET {url} failed after {retries + 1} attempts: {last_error}")

    def _fetch_once(self, url: str, accept: str, timeout: float, max_bytes: int | None) -> Response:
        req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT, "Accept": accept})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(max_bytes) if max_bytes else resp.read()
            return Response(url, resp.geturl(), resp.status, body, _lower(resp.headers), utc_now_iso())


def _lower(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    return {k.lower(): v for k, v in headers.items()}
