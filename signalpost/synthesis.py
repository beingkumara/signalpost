"""Short, decision-useful statements built only from available claims, each citing the claims it rests on.

Topics: what the company does, leadership, financials, public footprint, what Brønnøysund recorded as
changed, what changed since the previous check, and what remains unknown (and why).
No language model: every sentence is a template over sourced values, so nothing unsupported is added.
"""

from __future__ import annotations

import re
from typing import Any

from signalpost.explain import change_notes, money
from signalpost.modules import AVAILABLE

MIN_DESCRIPTION_CHARS = 30
_JUNK_DESCRIPTION = re.compile(
    r"powered by|just another wordpress|wordpress site|wix\.com|squarespace|coming soon|under construction|"
    r"kommer snart|lorem ipsum|my site|min side|default|velkommen til (vår|min) (nye )?(nett)?side",
    re.IGNORECASE)
_HOLDING = re.compile(r"holding|eie (og forvalte )?aksjer|kjøp og salg av aksjer|investering i (andre )?selskap|"
                      r"deltakelse i andre selskap|delta i andre selskap", re.IGNORECASE)
_ROLE_EN = {"Styre": "board", "Daglig leder": "CEO", "Regnskapsfører": "accountant", "Revisor": "auditor",
            "Forretningsfører": "business manager", "Innehaver": "proprietor", "Kontaktperson": "contact person",
            "Hovedforetak": "parent entity", "Komplementar": "general partner"}
_WEBSITE_FIELDS = {"website_description", "website_emails", "website_phones", "social_profiles", "brand_name",
                   "careers_page", "website_job_postings", "careers_platform", "website_news"}
MAX_EVENTS_NAMED = 3
MAX_UNKNOWNS_NAMED = 10


def synthesis(profile: dict[str, Any], claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by = {c["field"]: c for c in claims if c["availability"] == AVAILABLE}
    out: list[dict[str, Any]] = []

    def say(topic: str, text: str | None, fields: list[str]) -> None:
        ids = [by[f]["id"] for f in fields if f in by]
        if text and ids:
            out.append({"topic": topic, "text": text, "claim_ids": ids})

    say("what_it_does", _what_it_does(by), ["industry_codes", "purpose", "website_description"])
    leaders = [f"{lbl}: {by[f]['value'].get('name')}" for f, lbl in (("ceo", "CEO"), ("board_chair", "Chair"))
               if f in by and isinstance(by[f]["value"], dict)]
    say("leadership", "; ".join(leaders) + "." if leaders else None, ["ceo", "board_chair"])
    if "revenue" in by or "net_profit" in by:
        say("financials", finance_text(by), ["accounts_period", "revenue", "operating_profit", "net_profit", "equity"])
    say("public_footprint", _footprint(by),
        ["official_website", "social_profiles", "website_news", "job_postings", "website_job_postings",
         "careers_platform"])
    say("official_changes", _official_changes(by), ["official_changes", "registry_events"])
    changes = profile.get("changes_this_run", [])
    if changes:
        ids = [by[c["field"]]["id"] for c in changes if c["field"] in by]
        out.append({"topic": "changes", "text": f"{len(changes)} change(s) since the previous check: "
                    + " ".join(change_notes(changes[:5])), "claim_ids": ids[:10]})
    unknowns = _unknowns(claims)
    if unknowns:
        out.append(unknowns)
    return out


def _what_it_does(by: dict[str, dict[str, Any]]) -> str | None:
    industry = (by.get("industry_codes") or {}).get("value") or []
    purpose = (by.get("purpose") or {}).get("value")
    description = (by.get("website_description") or {}).get("value")
    parts: list[str] = []
    if industry:
        first = industry[0]
        label = first.get("description_en") or first.get("description") or "an unlabelled industry"
        text = f"Registered industry: {label} ({first.get('code', '?')})"
        extra = [i.get("description_en") or i.get("description") for i in industry[1:] if i]
        if extra:
            text += "; also " + "; ".join(e for e in extra if e)
        parts.append(text + ".")
    if isinstance(purpose, str) and _HOLDING.search(purpose):
        parts.append("Its registered purpose describes a holding company that owns shares in other companies.")
    if isinstance(purpose, str) and purpose.strip():
        parts.append(f"Stated purpose (Norwegian, from the register): {purpose.strip()}")
    if isinstance(description, str) and _useful_description(description):
        parts.append(f"On its own website it says: {description.strip()}")
    return " ".join(parts) or None


def _useful_description(text: str) -> bool:
    return len(text.strip()) >= MIN_DESCRIPTION_CHARS and not _JUNK_DESCRIPTION.search(text)


def finance_text(by: dict[str, dict[str, Any]]) -> str:
    period = (by.get("accounts_period") or {}).get("value") or {}
    parts = [f"{label} {money(by[f]['value']['amount'], by[f]['value']['currency'])}"
             for f, label in (("revenue", "revenue"), ("operating_profit", "operating result"),
                              ("net_profit", "net result"), ("equity", "equity"))
             if f in by and isinstance(by[f].get("value"), dict) and "amount" in by[f]["value"]]
    text = f"Filed accounts for {period.get('year', '?')}: " + ", ".join(parts) + "."
    equity = (by.get("equity") or {}).get("value", {}).get("amount")
    if isinstance(equity, (int, float)) and equity < 0:
        text += " Equity is negative."
    net = (by.get("net_profit") or {}).get("value", {}).get("amount")
    if isinstance(net, (int, float)):
        text += (" The year ended with a loss." if net < 0 else " The year ended with a profit." if net > 0
                 else " The company broke even (net result zero).")
    return text


def _footprint(by: dict[str, dict[str, Any]]) -> str | None:
    parts = []
    if "official_website" in by:
        parts.append(f"verified website {by['official_website']['value'].get('final_url')}")
    if "social_profiles" in by:
        parts.append("profiles on " + ", ".join(sorted(by["social_profiles"]["value"])))
    if "website_news" in by:
        latest = by["website_news"]["value"][0]
        parts.append(f"latest dated news on its website {latest.get('date')} (\"{latest.get('title')}\")")
    if "job_postings" in by:
        parts.append(f"{len(by['job_postings']['value'])} active job posting(s) on NAV")
    if "website_job_postings" in by:
        parts.append(f"{len(by['website_job_postings']['value'])} job posting(s) on its own website")
    if "careers_platform" in by:
        parts.append("recruits through " + ", ".join(sorted(by["careers_platform"]["value"])))
    return ("Public footprint: " + "; ".join(parts) + ".") if parts else None


def _official_changes(by: dict[str, dict[str, Any]]) -> str | None:
    events = (by.get("official_changes") or {}).get("value") or []
    if not events:
        return None
    roles = {}
    for event in (by.get("registry_events") or {}).get("value") or []:
        if event.get("type") == "roles_changed":
            role = (event.get("description") or "").replace(" last changed", "")
            roles.setdefault(event.get("date"), []).append(_ROLE_EN.get(role, role))
    material = [e for e in events if e.get("material")]
    text = f"Brønnøysund recorded {len(events)} update(s) in the last 12 months"
    if not material:
        return text + ", none of them material (routine refreshes such as employee counts)."
    named = "; ".join(_event_text(e, roles) for e in material[:MAX_EVENTS_NAMED])
    more = f" and {len(material) - MAX_EVENTS_NAMED} more" if len(material) > MAX_EVENTS_NAMED else ""
    return f"{text}, {len(material)} material: {named}{more}."


def _event_text(event: dict[str, Any], roles: dict[str, list[str]]) -> str:
    date = event.get("date")
    if event.get("type") == "roles_updated":
        who = roles.get(date)
        return f"{date}: {', '.join(sorted(set(who)))} changed" if who else f"{date}: roles changed"
    values = []
    for detail in event.get("details") or []:
        value = detail.get("value")
        if isinstance(value, (str, int, float)) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", str(value)) \
                and detail.get("op") != "remove":
            values.append(f"{detail.get('field')} → {value}")
    if values:
        return f"{date}: " + ", ".join(dict.fromkeys(values[:2]))
    return f"{date}: " + ", ".join(event.get("fields") or [event.get("type", "update")])


def _unknowns(claims: list[dict[str, Any]]) -> dict[str, Any] | None:
    gaps = [c for c in claims if c["availability"] != AVAILABLE and c.get("label")]
    if not gaps:
        return None
    no_site = any(c["field"] == "official_website" for c in gaps)
    lead = ("No company website could be verified, so website contacts, social profiles, news and careers "
            "information are unknown. " if no_site else "")
    rest = sorted({c["label"] for c in gaps if not (no_site and (c["field"] in _WEBSITE_FIELDS
                                                                 or c["field"] == "official_website"))})
    tail = ("Also not found or not applicable: " + ", ".join(rest[:MAX_UNKNOWNS_NAMED])
            + (f" and {len(rest) - MAX_UNKNOWNS_NAMED} more" if len(rest) > MAX_UNKNOWNS_NAMED else "") + "."
            if rest else "")
    return {"topic": "unknowns", "text": (lead + tail).strip(), "claim_ids": [c["id"] for c in gaps][:12]}
