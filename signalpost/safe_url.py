"""Outbound URL policy for untrusted URLs (company websites, links found on pages).

Only http/https on default ports to hosts that resolve to public addresses. This blocks
requests to localhost, private networks and cloud metadata endpoints (SSRF).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443}


class UnsafeUrl(ValueError):
    """Raised for URLs the agent refuses to fetch."""


def check_url(url: str, *, resolve: bool = True) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrl(f"scheme not allowed: {url}")
    if parsed.username or parsed.password:
        raise UnsafeUrl(f"credentials in URL not allowed: {url}")
    try:
        port = parsed.port
    except ValueError as err:
        raise UnsafeUrl(f"bad port in {url}") from err
    if port not in ALLOWED_PORTS:
        raise UnsafeUrl(f"port not allowed: {url}")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host or "." not in host or host.endswith((".local", ".internal", ".localhost")):
        raise UnsafeUrl(f"host not allowed: {url}")
    if _is_ip(host):
        _assert_public(host, url)
    elif resolve:
        for address in resolve_host(host):
            _assert_public(address, url)
    return url


def resolve_host(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError) as err:
        raise UnsafeUrl(f"host does not resolve: {host}") from err
    return sorted({info[4][0] for info in infos})


def host_resolves(host: str) -> bool:
    try:
        return bool(resolve_host(host))
    except UnsafeUrl:
        return False


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _assert_public(address: str, url: str) -> None:
    ip = ipaddress.ip_address(address.split("%")[0])
    if not ip.is_global or ip.is_multicast:
        raise UnsafeUrl(f"{url} resolves to non-public address {address}")
