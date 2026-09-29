"""Build the terminal result envelope (Builderr output contract) from a stored profile."""

from __future__ import annotations

from typing import Any

from signalpost.explain import change_notes, money
from signalpost.modules import AVAILABLE, FAILED, NOT_APPLICABLE, NOT_AVAILABLE

CONFIDENCE = {"high": 0.97, "medium": 0.8, "low": 0.5}

# section -> [(field, owning module, label)]
EXPECTED: dict[str, list[tuple[str, str, str]]] = {
    "legal_identity_and_brand": [
        ("name", "registry", "Registered name"), ("organization_form", "registry", "Organisation form"),
        ("business_address", "registry", "Business address"), ("industry_codes", "registry", "Industry codes"),
        ("founded_date", "registry", "Founded"), ("employees", "registry", "Registered employees"),
        ("share_capital", "registry", "Share capital"), ("purpose", "registry", "Stated purpose / activity"),
        ("brand_name", "website", "Public brand name"),
    ],
    "annual_accounts": [
        ("accounts_period", "annual_accounts", "Accounting period"), ("revenue", "annual_accounts", "Revenue"),
        ("operating_profit", "annual_accounts", "Operating profit"), ("net_profit", "annual_accounts", "Net profit"),
        ("equity", "annual_accounts", "Equity"), ("total_assets", "annual_accounts", "Total assets"),
        ("total_debt", "annual_accounts", "Total debt"),
        ("filing_history", "filing_history", "Filed annual accounts history"),
    ],
    "leadership_and_workplaces": [
        ("ceo", "roles", "Daily manager (CEO)"), ("board_chair", "roles", "Chair of the board"),
        ("board_members", "roles", "Board members"), ("auditor", "roles", "Auditor"),
        ("subunits", "workplaces", "Registered workplaces"),
    ],
    "website_and_profiles": [
        ("official_website", "website", "Verified official website"),
        ("website_description", "website", "Self-description on website"),
        ("website_emails", "website", "Contact emails on website"),
        ("website_phones", "website", "Phone numbers on website"),
        ("social_profiles", "website", "Company-owned social profiles"),
    ],
    "hiring_and_activity": [
        ("job_postings", "jobs", "Active job postings"), ("careers_page", "website", "Careers page"),
        ("website_job_postings", "website", "Job postings on company website"),
        ("website_news", "website", "Dated news on company website"),
        ("registry_events", "registry", "Dated events in official records"),
        ("official_changes", "official_changes", "Changes recorded by Brønnøysund (last 12 months)"),
    ],
}
_SECTION_OF = {f: s for s, rows in EXPECTED.items() for f, _, _ in rows}
_SECTION_OF.update({"website": "legal_identity_and_brand", "email": "legal_identity_and_brand",
                    "phone": "legal_identity_and_brand", "mobile": "legal_identity_and_brand",
                    "website_title": "website_and_profiles", "website_verification": "website_and_profiles",
                    "roles": "leadership_and_workplaces", "accountant": "leadership_and_workplaces",
                    "owner": "leadership_and_workplaces", "contact_person": "leadership_and_workplaces",
                    "subunit_count": "leadership_and_workplaces", "hiring": "hiring_and_activity",
                    "careers_platform": "hiring_and_activity",
                    "parent_orgnr": "leadership_and_workplaces", "parent_name": "leadership_and_workplaces"})
_ACCOUNT_PREFIXES = ("revenue", "operating", "net_", "profit", "total_", "fixed_", "current_", "equity",
                     "short_", "long_", "accounts_period", "group_")
_OWNER_ONLY_FORMS = {"ENK"}
_NO_BOARD = {"ceo", "board_chair", "board_members"}


def section_for(field: str) -> str:
    if field in _SECTION_OF:
        return _SECTION_OF[field]
    if field.startswith(_ACCOUNT_PREFIXES):
        return "annual_accounts"
    return "legal_identity_and_brand"


def build_envelope(profile: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    evidence, claims = _claims_and_evidence(profile)
    claims += _missing_claims(profile, {c["field"] for c in claims}, len(claims))
    sections = _sections(claims)
    envelope = {
        "organisation_number": profile["orgnr"],
        "name": profile.get("name"),
        "schema_version": profile.get("schema_version"),
        "agent_version": profile.get("agent_version"),
        "run": {**run, "terminal_status": "completed" if profile["status"] not in ("lookup_failed",) else "failed"},
        "state": _entity_state(profile["status"]),
        "company_status": profile["status"],
        "sections": sections,
        "claims": claims,
        "evidence": evidence,
        "availability": {m["module"]: {k: v for k, v in m.items() if k in ("state", "reason")}
                         for m in profile.get("modules", [])},
        "summary": {**profile.get("explanation", {}), "statements": synthesis(profile, claims)},
        "changes": profile.get("changes_this_run", []),
        "refresh": {
            "checked_at": profile.get("checked_at"),
            "previous_checked_at": profile.get("previous_checked_at"),
            "first_checked_at": profile.get("first_checked_at"),
            "revision": profile.get("revision", 1),
            "material_changes": sum(1 for c in profile.get("changes_this_run", []) if c.get("material")),
            "prior_evidence_preserved": len(profile.get("change_history", [])),
            "official_changes_last_365_days": len((profile.get("fields") or {}).get("official_changes") or []),
            "latest_official_change": ((profile.get("fields") or {}).get("official_changes") or [{}])[0].get("date"),
        },
        "errors": profile.get("warnings", []),
        "operations": profile.get("operations", {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0.0}),
    }
    return envelope


def _entity_state(status: str) -> str:
    return {"not_found": NOT_AVAILABLE, "invalid_input": FAILED, "lookup_failed": FAILED}.get(status, AVAILABLE)


def _claims_and_evidence(profile: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    index: dict[tuple, str] = {}
    claims: list[dict[str, Any]] = []
    for fact in profile.get("facts", []):
        ev = fact.get("evidence", {})
        src = fact.get("source", {})
        key = (src.get("url"), src.get("content_sha256"), ev.get("json_path"), ev.get("claim_span"))
        if key not in index:
            index[key] = f"e{len(evidence) + 1}"
            evidence.append({
                "id": index[key], "source_name": src.get("name"), "source_url": src.get("url"),
                "human_url": src.get("human_url"), "source_class": src.get("source_class"),
                "retrieved_at": fact.get("retrieved_at"), "content_sha256": src.get("content_sha256"),
                "extraction_method": fact.get("extraction_method"), "json_path": ev.get("json_path"),
                "claim_span": ev.get("claim_span"),
                "identity_proof": {k: ev[k] for k in ("matched_orgnr", "identity", "proof_url") if k in ev},
            })
        claim = {
            "id": f"c{len(claims) + 1}", "field": fact["field"], "section": section_for(fact["field"]),
            "label": fact.get("label"), "value": fact["value"], "availability": AVAILABLE,
            "confidence": CONFIDENCE.get(fact.get("confidence", "high"), 0.8), "evidence_ids": [index[key]],
            "as_of": fact.get("as_of"), "as_of_basis": fact.get("as_of_basis"),
            "first_seen": fact.get("first_seen"),
        }
        period = ev.get("period")
        if period:
            claim["reporting_period"] = period
        if fact.get("stale"):
            claim["stale"] = True
            claim["stale_reason"] = fact.get("stale_reason")
        claims.append(claim)
    return evidence, claims


def _missing_claims(profile: dict[str, Any], present: set[str], offset: int) -> list[dict[str, Any]]:
    modules = {m["module"]: m for m in profile.get("modules", [])}
    form = ((profile.get("fields") or {}).get("organization_form") or {}).get("code")
    out: list[dict[str, Any]] = []
    for section, rows in EXPECTED.items():
        for field, module, label in rows:
            if field in present:
                continue
            state, reason = _missing_state(profile, modules.get(module), module, field, form)
            out.append({"id": f"c{offset + len(out) + 1}", "field": field, "section": section, "label": label,
                        "value": None, "availability": state, "reason": reason, "evidence_ids": []})
    return out


def _missing_state(profile: dict[str, Any], module: dict[str, Any] | None, name: str, field: str,
                   form: str | None) -> tuple[str, str]:
    if profile["status"] in ("not_found", "invalid_input", "lookup_failed"):
        return (NOT_AVAILABLE if profile["status"] == "not_found" else FAILED), profile["status"]
    if form in _OWNER_ONLY_FORMS and field in _NO_BOARD:
        return NOT_APPLICABLE, f"organisation form {form} has a proprietor, not a board or CEO"
    if field == "auditor" and (profile.get("fields") or {}).get("audit_opt_out"):
        return NOT_APPLICABLE, "company has opted out of statutory audit"
    if module is None:
        return NOT_AVAILABLE, f"{name} source not checked for this entity type"
    if module["state"] == AVAILABLE:
        return NOT_AVAILABLE, f"{name} source checked; this field was not present"
    return module["state"], module.get("reason") or module["state"]


def _sections(claims: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for section in EXPECTED:
        rows = [c for c in claims if c["section"] == section]
        available = [c for c in rows if c["availability"] == AVAILABLE]
        states = sorted({c["availability"] for c in rows if c["availability"] != AVAILABLE})
        out[section] = {"state": AVAILABLE if available else (states[0] if states else NOT_AVAILABLE),
                        "available_claims": len(available), "unavailable_states": states,
                        "claim_ids": [c["id"] for c in rows]}
    return out


def synthesis(profile: dict[str, Any], claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Short supported statements, each citing the claims it rests on."""
    by = {c["field"]: c for c in claims if c["availability"] == AVAILABLE}
    out: list[dict[str, Any]] = []

    def say(topic: str, text: str, fields: list[str]) -> None:
        ids = [by[f]["id"] for f in fields if f in by]
        if ids:
            out.append({"topic": topic, "text": text, "claim_ids": ids})

    if "website_description" in by:
        say("what_it_does", f"The company describes itself as: {by['website_description']['value']}",
            ["website_description", "official_website"])
    elif "purpose" in by or "industry_codes" in by:
        industry = (by.get("industry_codes") or {}).get("value") or [{}]
        purpose = (by.get("purpose") or {}).get("value")
        label = industry[0].get("description_en") or industry[0].get("description") or ""
        extra = [i.get("description_en") or i.get("description") for i in industry[1:] if i]
        text = f"Works in {label.lower()} (industry code {industry[0].get('code', '?')})"
        if extra:
            text += "; also registered for " + "; ".join(e.lower() for e in extra if e)
        say("what_it_does", text + (f". Stated purpose (Norwegian, from the register): {purpose}" if purpose else "."),
            ["industry_codes", "purpose"])
    leaders = [f"{lbl}: {by[f]['value'].get('name')}" for f, lbl in (("ceo", "CEO"), ("board_chair", "Chair"))
               if f in by and isinstance(by[f]["value"], dict)]
    if leaders:
        say("leadership", "; ".join(leaders) + ".", ["ceo", "board_chair"])
    if "revenue" in by or "net_profit" in by:
        say("financials", _finance_text(by), ["accounts_period", "revenue", "net_profit", "equity"])
    footprint = []
    if "official_website" in by:
        footprint.append(f"verified website {by['official_website']['value'].get('final_url')}")
    if "social_profiles" in by:
        footprint.append("profiles on " + ", ".join(sorted(by["social_profiles"]["value"])))
    if "job_postings" in by:
        footprint.append(f"{len(by['job_postings']['value'])} active job posting(s) on NAV")
    if "website_job_postings" in by:
        footprint.append(f"{len(by['website_job_postings']['value'])} job posting(s) on its own website")
    if "careers_platform" in by:
        footprint.append("recruits through " + ", ".join(sorted(by["careers_platform"]["value"])))
    if footprint:
        say("public_footprint", "Public footprint: " + "; ".join(footprint) + ".",
            ["official_website", "social_profiles", "job_postings", "website_job_postings", "careers_platform"])
    official = by.get("official_changes")
    if official:
        events = official["value"]
        material = [e for e in events if e.get("material")]
        latest = events[0]
        what = ", ".join(latest.get("fields") or [latest.get("type", "update")])
        text = (f"Brønnøysund recorded {len(events)} update(s) in the last 12 months"
                f"{f', {len(material)} of them material' if material else ''}; most recent on {latest['date']} "
                f"({what}).")
        out.append({"topic": "official_changes", "text": text, "claim_ids": [official["id"]]})
    changes = profile.get("changes_this_run", [])
    if changes:
        ids = [by[c["field"]]["id"] for c in changes if c["field"] in by]
        out.append({"topic": "changes", "text": f"{len(changes)} change(s) since the previous check: "
                    + " ".join(change_notes(changes[:5])), "claim_ids": ids[:10]})
    unknown = sorted({c["label"] for c in claims if c["availability"] != AVAILABLE and c.get("label")})
    if unknown:
        out.append({"topic": "unknowns", "text": "Not found or not applicable: " + ", ".join(unknown[:12]) + ".",
                    "claim_ids": [c["id"] for c in claims if c["availability"] != AVAILABLE][:12]})
    return out


def _finance_text(by: dict[str, dict[str, Any]]) -> str:
    period = (by.get("accounts_period") or {}).get("value") or {}
    parts = [f"{label} {money(by[f]['value']['amount'], by[f]['value']['currency'])}"
             for f, label in (("revenue", "revenue"), ("net_profit", "net result"), ("equity", "equity"))
             if f in by]
    text = f"Filed accounts for {period.get('year', '?')}: " + ", ".join(parts) + "."
    equity = (by.get("equity") or {}).get("value", {}).get("amount")
    if isinstance(equity, (int, float)) and equity < 0:
        text += " Equity is negative."
    net = (by.get("net_profit") or {}).get("value", {}).get("amount")
    if isinstance(net, (int, float)):
        text += " The year ended with a loss." if net < 0 else " The year ended with a profit."
    return text
