"""Stdlib HTTP client: hard request budget, content hashes, latency metrics, safe redirects."""

from __future__ import annotations

import hashlib
import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from signalpost import config
from signalpost.safe_url import UnsafeUrl, check_url


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

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("content-type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def normalise_url(url: str) -> str:
    """Percent-encode spaces and non-ASCII characters, and IDNA-encode the host, so urllib accepts the URL."""
    parsed = urlparse(url.strip())
    host = parsed.hostname or ""
    try:
        ascii_host = host.encode("idna").decode("ascii") if host else host
    except UnicodeError:
        ascii_host = host
    netloc = parsed.netloc.replace(host, ascii_host) if host else parsed.netloc
    safe = "/:@!$&'()*+,;=%~-._"
    return urlunparse((parsed.scheme, netloc, quote(parsed.path, safe=safe), parsed.params,
                       quote(parsed.query, safe=safe + "?"), ""))


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect target against the outbound URL policy."""

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if getattr(req, "untrusted", False):
            check_url(newurl)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.untrusted = getattr(req, "untrusted", False)
        return new


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """API calls carry keys: never follow a redirect (it could forward the key to another host)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_SafeRedirects())
_API_OPENER = urllib.request.build_opener(_NoRedirects())


class HttpClient:
    """Thread-safe client. Every network attempt counts against the budget."""

    def __init__(self, budget: int = config.DEFAULT_REQUEST_BUDGET) -> None:
        self._budget = budget
        self._used = 0
        self._bytes = 0
        self._latencies: list[int] = []
        self._lock = threading.Lock()
        self._host_next: dict[str, float] = {}
        self._host_cooldown: dict[str, float] = {}
        self._host_locks: dict[str, threading.Lock] = {}

    @property
    def budget(self) -> int:
        return self._budget

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self._budget - self._used)

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            lat = sorted(self._latencies)
        return {
            "requests": self._used,
            "bytes_received": self._bytes,
            "p50_ms": lat[len(lat) // 2] if lat else None,
            "p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
        }

    def _reserve(self) -> None:
        with self._lock:
            if self._used >= self._budget:
                raise BudgetExceeded(f"request budget of {self._budget} exhausted")
            self._used += 1

    def _host_gate(self, url: str) -> Response | None:
        """Pace polite hosts; return a synthetic 429 while a host is cooling down (no request made)."""
        host = (urlparse(url).hostname or "").lower()
        interval = config.HOST_MIN_INTERVAL_SECONDS.get(host)
        if interval is None:
            return None
        with self._lock:
            if self._host_cooldown.get(host, 0) > time.monotonic():
                return Response(url, url, 429, b"", {"x-signalpost": "host cooling down after HTTP 429"},
                                utc_now_iso())
            lock = self._host_locks.setdefault(host, threading.Lock())
        with lock:
            wait = self._host_next.get(host, 0) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._host_next[host] = time.monotonic() + interval
        return None

    def _note_status(self, url: str, status: int) -> None:
        host = (urlparse(url).hostname or "").lower()
        if status == 429 and host in config.HOST_COOLDOWN_SECONDS:
            with self._lock:
                self._host_cooldown[host] = time.monotonic() + config.HOST_COOLDOWN_SECONDS[host]

    def _record(self, started: float, size: int) -> None:
        with self._lock:
            self._latencies.append(int((time.monotonic() - started) * 1000))
            self._bytes += size

    def get(
        self,
        url: str,
        *,
        accept: str = "application/json",
        timeout: float = config.HTTP_TIMEOUT_SECONDS,
        max_bytes: int | None = None,
        retries: int = config.HTTP_RETRIES,
        untrusted: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """GET with retries on 429/5xx/network errors. 4xx responses are returned, not raised.

        `untrusted=True` applies the outbound URL policy to the URL and every redirect.
        """
        url = normalise_url(url)
        if untrusted:
            try:
                check_url(url)
            except UnsafeUrl as err:
                raise ConnectionError(f"refused unsafe URL: {err}") from err
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            gated = self._host_gate(url)
            if gated is not None:
                return gated
            self._reserve()
            started = time.monotonic()
            try:
                resp = self._fetch_once(url, accept, timeout, max_bytes, untrusted, headers or {})
                self._record(started, len(resp.body))
                return resp
            except urllib.error.HTTPError as err:
                body = err.read() if err.fp else b""
                self._record(started, len(body))
                self._note_status(url, err.code)
                polite_host = urlparse(url).hostname in config.HOST_COOLDOWN_SECONDS
                if err.code == 429 and polite_host:
                    return Response(url, url, 429, body, _lower(err.headers), utc_now_iso())  # no retry
                if (err.code == 429 or err.code >= 500) and attempt < retries:
                    last_error = err
                else:
                    return Response(url, url, err.code, body, _lower(err.headers), utc_now_iso())
            except UnsafeUrl as err:
                raise ConnectionError(f"refused unsafe redirect: {err}") from err
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError, OSError,
                    ValueError) as err:
                self._record(started, 0)
                last_error = err
            if attempt < retries:
                time.sleep(config.RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise ConnectionError(f"GET {url} failed after {retries + 1} attempts: {last_error}")

    def post_json(self, url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None,
                  timeout: float = config.HTTP_TIMEOUT_SECONDS, retries: int = 1) -> Response:
        """POST a JSON body to a trusted API host. Counts against the budget like GET; 4xx are returned."""
        body = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            self._reserve()
            started = time.monotonic()
            try:
                resp = self._fetch_once(url, "application/json", timeout, None, False,
                                        {"Content-Type": "application/json", **(headers or {})}, data=body,
                                        opener=_API_OPENER)
                self._record(started, len(resp.body))
                return resp
            except urllib.error.HTTPError as err:
                err_body = err.read() if err.fp else b""
                self._record(started, len(err_body))
                if err.code >= 500 and attempt < retries:
                    last_error = err
                else:
                    return Response(url, url, err.code, err_body, _lower(err.headers), utc_now_iso())
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError, OSError,
                    ValueError) as err:
                self._record(started, 0)
                last_error = err
            if attempt < retries:
                time.sleep(config.RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise ConnectionError(f"POST {url} failed after {retries + 1} attempts: {last_error}")

    def _fetch_once(self, url: str, accept: str, timeout: float, max_bytes: int | None, untrusted: bool,
                    extra_headers: dict[str, str], data: bytes | None = None,
                    opener: urllib.request.OpenerDirector | None = None) -> Response:
        req = urllib.request.Request(url, data=data, headers={"User-Agent": config.USER_AGENT, "Accept": accept,
                                                              "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
                                                              **extra_headers})
        req.untrusted = untrusted  # type: ignore[attr-defined]
        with (opener or _OPENER).open(req, timeout=timeout) as resp:
            body = resp.read(max_bytes) if max_bytes else resp.read()
            return Response(url, resp.geturl(), resp.status, body, _lower(resp.headers), utc_now_iso())


def _lower(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    return {k.lower(): v for k, v in headers.items()}
