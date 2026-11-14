"""Build the terminal result envelope (Builderr output contract) from a stored profile."""

from __future__ import annotations

from typing import Any

from signalpost.item_checks import CANT_TELL
from signalpost.modules import AMBIGUOUS, AVAILABLE, FAILED, NOT_APPLICABLE, NOT_AVAILABLE
from signalpost.redact import redact_hosts
from signalpost.synthesis import synthesis

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
        ("careers_platform", "website", "Recruitment platform"),
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
    explanation = profile.get("explanation", {})
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
        "availability": {m["module"]: {"state": m.get("state"), "reason": redact_hosts(m.get("reason"))}
                         for m in profile.get("modules", [])},
        "summary": {**explanation, "notes": [redact_hosts(n) for n in explanation.get("notes") or []],
                    "statements": synthesis(profile, claims)},
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
        "errors": [redact_hosts(w) for w in profile.get("warnings", [])],
        "operations": profile.get("operations", {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0.0}),
    }
    return envelope


def _entity_state(status: str) -> str:
    return {"not_found": NOT_AVAILABLE, "invalid_input": FAILED, "lookup_failed": FAILED}.get(status, AVAILABLE)


def _withheld(profile: dict[str, Any]) -> dict[str, str]:
    """Fields (or some of their entries) left out as "can't tell", with the reason, across modules."""
    out: dict[str, str] = {}
    for module in profile.get("modules", []):
        out.update(module.get("withheld") or {})
    return out


def _claims_and_evidence(profile: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    withheld = _withheld(profile)
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
        if fact["field"] in withheld:
            claim["note"] = redact_hosts(f"{CANT_TELL}: {withheld[fact['field']]}")
        if fact.get("stale"):
            claim["stale"] = True
            claim["stale_reason"] = redact_hosts(fact.get("stale_reason"))
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
                        "value": None, "availability": state, "reason": redact_hosts(reason), "evidence_ids": []})
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
    if field in (module.get("withheld") or {}):
        return AMBIGUOUS, f"{CANT_TELL}: {module['withheld'][field]}"
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
