"""Deterministic, template-based explanations.

No language model is involved, so an explanation can only repeat sourced facts.
"""

from __future__ import annotations

from typing import Any

from signalpost.labels import org_form_en, with_article

_STATUS_TEXT = {
    "active": "is an active entity in Enhetsregisteret",
    "deleted": "has been deleted from Enhetsregisteret",
    "bankrupt": "is registered as bankrupt (konkurs)",
    "under_liquidation": "is under voluntary liquidation",
    "under_forced_dissolution": "is under forced liquidation or dissolution",
    "subunit": "is a sub-unit (underenhet), i.e. an establishment of another legal entity",
}

_NO_ACCOUNTS_FORMS = {"ENK", "NUF", "ANS", "DA", "FLI", "ORGL", "STAT", "KOMM", "FYLK", "KIRK", "ESEK", "SAM"}


def explain(profile: dict[str, Any]) -> dict[str, Any]:
    status = profile.get("status")
    orgnr = profile.get("orgnr", "")
    if status == "invalid_input":
        return {"summary": f"'{orgnr}' is not a valid Norwegian organisation number, so nothing was looked up.",
                "notes": profile.get("warnings", [])}
    if status == "not_found":
        return {"summary": f"No entity with organisation number {orgnr} exists in Enhetsregisteret. "
                           "No facts were published to avoid attributing another company's data.",
                "notes": []}
    if status == "lookup_failed":
        return {"summary": f"The registry lookup for {orgnr} failed, so no facts were published.",
                "notes": profile.get("warnings", [])}

    f = profile.get("fields", {})
    sentences = [_intro(profile, f), _industry(f), _people(f), _finance(f), _web(f)]
    summary = " ".join(s for s in sentences if s)
    return {"summary": summary, "notes": _notes(profile, f)}


def _intro(profile: dict[str, Any], f: dict[str, Any]) -> str:
    form_data = f.get("organization_form") or {}
    form = org_form_en(form_data.get("code"), form_data.get("description"))
    name = profile.get("name") or "The entity"
    text = f"{name} ({profile.get('orgnr_formatted', profile.get('orgnr'))})"
    text += f", {with_article(form)}," if form else ""
    text += f" {_STATUS_TEXT.get(profile.get('status', ''), 'is registered')}"
    if f.get("founded_date"):
        text += f"; founded {f['founded_date']}"
    address = (f.get("business_address") or f.get("location_address") or {}).get("text")
    if address:
        text += f"; address {address}"
    return text + "."


def _industry(f: dict[str, Any]) -> str:
    parts = []
    codes = f.get("industry_codes") or []
    if codes:
        parts.append(f"Main industry is {codes[0]['code']} {codes[0].get('description') or ''}".rstrip())
    if f.get("employees") is not None:
        parts.append(f"{f['employees']:,} registered employees")
    if f.get("subunit_count"):
        parts.append(f"{f['subunit_count']} registered sub-unit(s)")
    return ("; ".join(parts) + ".") if parts else ""


def _people(f: dict[str, Any]) -> str:
    parts = []
    for key, label in (("ceo", "Daily manager"), ("board_chair", "Board chair"), ("owner", "Proprietor"),
                       ("auditor", "Auditor")):
        holder = f.get(key)
        if isinstance(holder, dict):
            parts.append(f"{label}: {holder.get('name')}")
        elif isinstance(holder, list) and holder:
            parts.append(f"{label}: {', '.join(h.get('name', '') for h in holder)}")
    return ("; ".join(parts) + ".") if parts else ""


def _finance(f: dict[str, Any]) -> str:
    period = f.get("accounts_period")
    if not period:
        return ""
    bits = []
    for key, label in (("revenue", "revenue"), ("operating_profit", "operating profit"),
                       ("net_profit", "net profit"), ("equity", "equity"), ("total_assets", "total assets")):
        value = f.get(key)
        if value:
            bits.append(f"{label} {money(value['amount'], value['currency'])}")
    if not bits:
        return ""
    return f"Accounts for {period.get('year')} (period ending {period.get('to')}): " + ", ".join(bits) + "."


def _web(f: dict[str, Any]) -> str:
    parts = []
    site = f.get("official_website")
    if isinstance(site, dict):
        proof = {"orgnr_on_page": "the org number appears on the site",
                 "name_on_page": "the registry lists it and the site shows the registered name"}
        parts.append(f"Official website {site.get('final_url')} is verified ({proof.get(site.get('verification'), '')})")
    elif f.get("website"):
        parts.append(f"The registry lists the website {f['website']}, but its content could not be verified")
    if f.get("social_profiles"):
        parts.append("company-owned profiles on " + ", ".join(sorted(f["social_profiles"])))
    if f.get("job_postings"):
        parts.append(f"{len(f['job_postings'])} active job posting(s) on arbeidsplassen.nav.no")
    return ("; ".join(parts) + ".") if parts else ""


def _notes(profile: dict[str, Any], f: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    form = (f.get("organization_form") or {}).get("code")
    status = profile.get("status")
    if status == "active" and not f.get("accounts_period"):
        if form in _NO_ACCOUNTS_FORMS:
            notes.append(f"No annual accounts: organisation form {form} is generally not required to file "
                         "with Regnskapsregisteret.")
        else:
            notes.append("No annual accounts were found in Regnskapsregisteret (new company, late filing, "
                         "or not obliged to file). No financial figures are shown.")
    if status == "subunit" and f.get("parent_orgnr"):
        parent = f"{f['parent_name']} ({f['parent_orgnr']})" if f.get("parent_name") else f["parent_orgnr"]
        notes.append(f"Roles and accounts belong to the parent entity {parent}; look it up for those.")
    if f.get("bankrupt"):
        notes.append("Bankruptcy is registered; figures and roles may describe the company before the estate "
                     "took over.")
    if f.get("deleted_date"):
        notes.append(f"Deleted on {f['deleted_date']}. Only the remaining register record is shown.")
    if f.get("group_accounts_period"):
        notes.append("Group (konsern) accounts are also shown with the prefix 'group_'.")
    notes.extend(profile.get("warnings", []))
    return notes


def money(amount: float, currency: str) -> str:
    size = abs(amount)
    for threshold, suffix in ((1e9, " bn"), (1e6, " m"), (1e3, " k")):
        if size >= threshold:
            return f"{currency} {amount / threshold:,.2f}{suffix}"
    return f"{currency} {amount:,.0f}"


def change_notes(changes: list[dict[str, Any]]) -> list[str]:
    """Human-readable lines for changes detected on this refresh."""
    lines = []
    for change in changes:
        if change.get("summary"):
            lines.append(change["summary"])
            continue
        kind = change.get("change")
        name = change.get("label") or change.get("field")
        if kind == "added":
            lines.append(f"New: {name}.")
        elif kind == "removed":
            lines.append(f"No longer present in the source: {name}.")
        else:
            lines.append(f"Changed: {name} (was {_short(change.get('old'))}, now {_short(change.get('new'))}).")
    return lines


def _short(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("text") or value.get("name") or value.get("amount") or value.get("code") or value
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."
