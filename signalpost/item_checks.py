"""Per-entry confidence and a one-line reason for news and job entries.

Every published news item or job ad carries `confidence` ("high" or "medium") and `reason`. When the reason
is weak the entry is not published: it is counted as "can't tell" with the reason it failed.

- News: a dated article on the company's verified site that names the company (legal name, brand or domain
  label) in its headline (high) or in its own text (medium). An article that does not name the company is
  "can't tell", because a group or partner site can carry other companies' news.
- Website job ads: a single schema.org JobPosting whose hiring organisation names this company. An ad without
  a hiring organisation is "can't tell": on a group careers page it could be any group company's opening.
- NAV job ads: the ad's employer org number equals the company's or one of its registered sub-units'.
- Careers pages and recruitment-platform links are not job entries on their own: they are published only when
  the same crawl found an opening for this company on the company's site.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from signalpost.webparse import (ascii_tokens, core_name, distinctive_name, merge_initials, registrable_domain,
                                 strip_site_suffix)

CANT_TELL = "can't tell"
_CONNECTORS = {"og", "and", "the", "of", "i"}
MIN_NAME_CHARS = 4
_LOGIN_WORDS = {"login", "logg", "logon", "signin", "sign", "auth", "dashboard", "minside", "my", "account",
                "konto", "admin", "portal", "sso"}
_LOGIN_FILLERS = {"inn", "in", "on", "side", "page", "user", "bruker"}
_RANK = {"high": 2, "medium": 1}


def _name_tokens(owner: str) -> list[str]:
    return [t for t in merge_initials(ascii_tokens(core_name(owner) or owner)) if len(t) > 1 and t not in _CONNECTORS]


def _domain_label(owner: str) -> str:
    host = owner.split("//", 1)[-1].split("/", 1)[0]
    return "".join(ascii_tokens(registrable_domain(host).split(".")[0]))


def _contains(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def _joined_in(hay: list[str], joined: str, max_parts: int = 4) -> bool:
    """`joined` equals one token or a run of adjacent tokens ('rbproffpartner', 'rb proff partner')."""
    for i in range(len(hay)):
        acc = ""
        for token in hay[i:i + max_parts]:
            acc += token
            if acc == joined:
                return True
            if not joined.startswith(acc):
                break
    return False


def names_company(text: str, owners: Sequence[str]) -> str | None:
    """The owner (legal name, brand or verified domain) that `text` names, or None.

    Whole-name matches only: one generic word of a longer name ('Bygg' in 'Acme Bygg AS') never counts."""
    hay = ascii_tokens(text or "")
    if not hay:
        return None
    for owner in owners:
        if not owner:
            continue
        if "." in owner and " " not in owner.strip():  # verified site URL or domain
            label = _domain_label(owner)
            if len(label) >= MIN_NAME_CHARS and _joined_in(hay, label):
                return f"{label.title()} (domain {registrable_domain(owner.split('//', 1)[-1].split('/', 1)[0])})"
            continue
        tokens = _name_tokens(owner)
        joined = "".join(tokens)
        if len(joined) < MIN_NAME_CHARS or (len(tokens) == 1 and not distinctive_name(owner)):
            continue
        if _contains(hay, tokens) or _joined_in(hay, joined):
            return core_name(owner) or owner
    return None


def cap(level: str, ceiling: str) -> str:
    """The lower of two confidence levels."""
    return level if _RANK.get(level, 0) <= _RANK.get(ceiling, 0) else ceiling


def lowest(levels: Sequence[str]) -> str:
    return min(levels, key=lambda lv: _RANK.get(lv, 0)) if levels else "medium"


def assess_news(item: dict[str, Any], owners: Sequence[str], site_confidence: str) -> dict[str, Any] | None:
    """The published item with confidence and reason, or None when we can't tell it is about this company."""
    public = {k: v for k, v in item.items() if k not in ("excerpt", "kind")}
    kind = item.get("kind") or "dated post"
    # A company name only in the title's suffix ('Valgresultat | Acme') is the site's name, not the article's.
    suffix_names = [_domain_label(o) if "." in o and " " not in o.strip() else o for o in owners if o]
    in_headline = names_company(strip_site_suffix(item.get("title") or "", *suffix_names), owners)
    if in_headline:
        return {**public, "confidence": cap("high", site_confidence),
                "reason": f"{kind} dated {item['date']} on the company's verified site; headline names {in_headline}"}
    in_text = names_company(item.get("excerpt") or "", owners)
    if in_text:
        return {**public, "confidence": "medium",
                "reason": f"{kind} dated {item['date']} on the company's verified site; article text names {in_text}"}
    return None


def assess_site_job(job: dict[str, Any], owners: Sequence[str], site_confidence: str) -> dict[str, Any] | None:
    public = {k: v for k, v in job.items() if k != "employer"}
    employer = job.get("employer")
    named = names_company(str(employer or ""), owners) if employer else None
    if not named:
        return None
    until = f", open until {job['expires']}" if job.get("expires") else ""
    return {**public, "confidence": cap("high", site_confidence),
            "reason": f"single JobPosting ad on the company's verified site naming {employer} as employer{until}"}


def nav_job_reason(posting: dict[str, Any], orgnr: str) -> str:
    employer = posting.get("employer_orgnr")
    who = "this company" if employer == orgnr else f"its registered sub-unit {employer}"
    until = f", open until {posting['expires']}" if posting.get("expires") else ""
    return f"NAV ad whose employer org number {employer} is {who}{until}"


def login_url(url: str) -> bool:
    """A sign-in, dashboard or account page, which shows no public opening.

    A host label ('login.acme.no', 'minside.acme.no') or a whole path segment made only of login words
    ('/logg-inn', '/my-account') counts; a job slug that merely contains one ('/admin-assistent') does not."""
    rest = url.split("?", 1)[0].split("#", 1)[0].lower().split("//", 1)[-1]
    host, _, path = rest.partition("/")
    labels = host.split(".")
    if len(labels) > 2 and labels[0] in _LOGIN_WORDS:
        return True
    for segment in path.split("/"):
        tokens = ascii_tokens(segment)
        if tokens and all(t in _LOGIN_WORDS | _LOGIN_FILLERS for t in tokens) and set(tokens) - _LOGIN_FILLERS:
            return True
    return False


# Parent-group and regional social accounts (Builderr feedback: "a few pages belonged to a parent group").
_REGION_WORDS = {"nordic", "nordics", "norden", "scandinavia", "scandinavian", "global", "international", "intl",
                 "group", "gruppen", "europe", "emea", "sverige", "sweden", "danmark", "denmark", "finland", "suomi",
                 "worldwide", "corporate", "hq"}
_NORWAY_MARKERS = {"norge", "norway", "no", "nor"}


def parent_group_accounts(accounts: dict[str, str], handles: dict[str, str], company_name: str) -> dict[str, str]:
    """Accounts that belong to a parent group or region rather than this company: network -> why.

    - The handle names a region or group ("…Nordics", "…Group") that is not part of the company's own name.
    - The site links Norway-marked accounts ("jysknorge", "JYSK.no") and unmarked ones ("jysk"): the unmarked
      ones are the international group's."""
    own_words = set(ascii_tokens(company_name))
    words = {net: _handle_words(handles.get(net, "")) for net in accounts}
    dropped: dict[str, str] = {}
    for net, tokens in words.items():
        region = sorted((tokens & _REGION_WORDS) - own_words)
        if region:
            dropped[net] = f"{net} handle names a region or group ('{region[0]}'), not this company"
    marked = {net for net, tokens in words.items() if tokens & _NORWAY_MARKERS and net not in dropped}
    if dropped and not own_words & _NORWAY_MARKERS:  # the site links a group's regional accounts
        for net in words:
            if net not in marked and net not in dropped:
                dropped[net] = (f"{net} account has no Norway marker on a site that links the group's regional "
                                "accounts: likely the group's")
    if marked and not own_words & _NORWAY_MARKERS:
        for net in words:
            if net not in marked and net not in dropped:
                dropped[net] = (f"{net} account has no Norway marker while the site's other accounts do "
                                f"({', '.join(sorted(marked))}): likely the international group's")
    return dropped


def _handle_words(handle: str) -> set[str]:
    """Words in a handle, also split off its end ('jysknorge' -> norge, 'NTTDATABusinessSolutionsNordics')."""
    tokens = set(ascii_tokens(handle))
    flat = "".join(ascii_tokens(handle))
    for word in _REGION_WORDS | _NORWAY_MARKERS - {"no", "nor"}:
        if len(word) >= 4 and flat.endswith(word) and flat != word:
            tokens.add(word)
    return tokens
