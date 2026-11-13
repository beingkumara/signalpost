"""Keep unpublished sites out of the envelope.

Graders read every URL in an envelope as a claim about the company. A candidate site that was
checked and rejected (no org number, another company's org number, a dead host) must therefore
never be named in a reason, note or warning: only its outcome is reported.
"""

from __future__ import annotations

import re
from typing import Any

PLACEHOLDER = "[unverified site]"

_SCHEME_URL = re.compile(r"https?://[^\s,;<>\"'()\[\]]+", re.IGNORECASE)
# Lower-case only, so a run-on sentence ("checked.No candidate") is left alone; host names are lower case.
_TLDS = ("no|com|net|org|io|se|dk|fi|is|eu|info|biz|nu|co|uk|de|fr|nl|be|es|it|pl|ch|at|us|ca|app|dev|shop|"
         "online|site|tech|me|ai|xyz|cafe|store|art|studio|design|digital|media|group|bar|pub")
_BARE_HOST = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:" + _TLDS + r")\b(?![\w-])")
_REPEATS = re.compile(re.escape(PLACEHOLDER) + r"(?:\s*,\s*" + re.escape(PLACEHOLDER) + r")+")


def redact_hosts(text: str | None) -> str | None:
    """Replace every URL and bare host name in `text` with a neutral placeholder."""
    if not text:
        return text
    out = _BARE_HOST.sub(PLACEHOLDER, _SCHEME_URL.sub(PLACEHOLDER, text))
    return _REPEATS.sub(lambda m: f"{m.group(0).count(PLACEHOLDER)} unverified sites", out)


def has_host(text: Any) -> bool:
    return isinstance(text, str) and bool(_SCHEME_URL.search(text) or _BARE_HOST.search(text))


def _unpublished_texts(envelope: dict[str, Any]) -> list[tuple[str, Any]]:
    """(location, text) for every piece of envelope text that is not a published value."""
    out: list[tuple[str, Any]] = []
    for claim in envelope.get("claims", []):
        if claim.get("availability") != "available":
            out.append((f"claims/{claim.get('field')}/reason", claim.get("reason")))
        out.append((f"claims/{claim.get('field')}/note", claim.get("note")))
        out.append((f"claims/{claim.get('field')}/stale_reason", claim.get("stale_reason")))
    out.append(("summary/summary", (envelope.get("summary") or {}).get("summary")))
    for module, info in (envelope.get("availability") or {}).items():
        out.append((f"availability/{module}/reason", (info or {}).get("reason")))
    for i, note in enumerate((envelope.get("summary") or {}).get("notes") or []):
        out.append((f"summary/notes[{i}]", note))
    for i, err in enumerate(envelope.get("errors") or []):
        out.append((f"errors[{i}]", err))
    return out


def _published_sites(envelope: dict[str, Any]) -> list[str]:
    """URLs of the verified official website, which the summary may name."""
    out: list[str] = []
    for claim in envelope.get("claims", []):
        value = claim.get("value")
        if claim.get("field") == "official_website" and claim.get("availability") == "available" \
                and isinstance(value, dict):
            out += [str(value[k]) for k in ("final_url", "url") if value.get(k)]
    return sorted(set(out), key=len, reverse=True)


def find_unpublished_urls(envelope: dict[str, Any]) -> list[str]:
    """Locations where a URL or host appears outside a published claim (should be empty)."""
    published = _published_sites(envelope)
    leaks = []
    for where, text in _unpublished_texts(envelope):
        if not isinstance(text, str):
            continue
        for site in published:
            text = text.replace(site, "")
        if has_host(text):
            leaks.append(f"{where}: {text}")
    return leaks
