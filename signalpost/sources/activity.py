"""Dated public activity derived from official records (no extra requests).

Each event points at the official record that dates it.
"""

from __future__ import annotations

from typing import Any

from signalpost import config
from signalpost.facts import Fact, make_fact


def registry_events(raw: dict[str, Any], role_facts: list[Fact], entity_fact: Fact) -> tuple[Fact, ...]:
    orgnr = raw.get("organisasjonsnummer", "")
    entity_url = config.ENHET_URL.format(orgnr=orgnr)
    events: list[dict[str, Any]] = []

    def add(date: str | None, kind: str, text: str, url: str = entity_url) -> None:
        if date:
            events.append({"date": date[:10], "type": kind, "description": text, "source_url": url})

    add(raw.get("registreringsdatoEnhetsregisteret"), "registered", "Registered in Enhetsregisteret")
    add(raw.get("stiftelsesdato"), "founded", "Company founded")
    add(raw.get("vedtektsdato"), "articles_updated", "Articles of association dated")
    capital = raw.get("kapital") or {}
    if capital.get("innfortDato"):
        add(capital["innfortDato"], "share_capital_registered",
            f"Share capital registered: {capital.get('belop')} {capital.get('valuta') or ''}".strip())
    for former in raw.get("historiskeNavn") or []:
        add(former.get("tilDato"), "name_change", f"Name changed from {former.get('navn')}")
    add(raw.get("konkursdato"), "bankruptcy", "Bankruptcy registered")
    add(raw.get("underAvviklingDato"), "liquidation", "Voluntary liquidation registered")
    if raw.get("sisteInnsendteAarsregnskap"):
        events.append({"date": None, "year": raw["sisteInnsendteAarsregnskap"], "type": "accounts_filed",
                       "description": f"Annual accounts for {raw['sisteInnsendteAarsregnskap']} submitted",
                       "source_url": entity_url})
    roles = next((f for f in role_facts if f.field == "roles"), None)
    if roles:
        seen: set[tuple[str, str]] = set()
        for role in roles.value:
            key = (role.get("group") or "", role.get("group_last_changed") or "")
            if key[1] and key not in seen:
                seen.add(key)
                add(key[1], "roles_changed", f"{key[0] or 'Roles'} last changed", roles.source.url)
    dated = sorted((e for e in events if e.get("date")), key=lambda e: e["date"], reverse=True)
    undated = [e for e in events if not e.get("date")]
    if not dated and not undated:
        return ()
    ordered = dated + undated
    return (make_fact("registry_events", ordered, entity_fact.source, entity_fact.retrieved_at,
                      json_path="(multiple dated fields)", as_of=dated[0]["date"] if dated else None,
                      as_of_basis="latest_event_date", label="Dated events in official records",
                      extra_evidence={"matched_orgnr": orgnr}, extraction_method="derived_from_official_fields"),)
