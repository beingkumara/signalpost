"""Roles (board, CEO, auditor, accountant ...) from Enhetsregisteret.

Birth dates are deliberately dropped: the profile needs who holds a role, not personal data.
"""

from __future__ import annotations

from typing import Any

from signalpost import config
from signalpost.facts import OFFICIAL_ROLES, Fact, Source, make_fact
from signalpost.http_client import HttpClient
from signalpost.modules import FAILED, ModuleResult, from_facts

# Summary fields for the most asked-for roles. Everything else lands in "roles".
_KEY_ROLES = {
    "DAGL": ("ceo", "Daily manager (CEO)"),
    "LEDE": ("board_chair", "Chair of the board"),
    "REVI": ("auditor", "Auditor"),
    "REGN": ("accountant", "Accountant"),
    "KONT": ("contact_person", "Contact person"),
    "INNH": ("owner", "Proprietor"),
}


def fetch_roles(client: HttpClient, orgnr: str) -> ModuleResult:
    url = config.ROLLER_URL.format(orgnr=orgnr)
    resp = client.get(url)
    if resp.status == 404:
        return from_facts("roles", (), url, resp.fetched_at, "no roles registered")
    if resp.status != 200:
        return ModuleResult("roles", FAILED, (), f"HTTP {resp.status}", url, resp.fetched_at)
    source = Source(config.SOURCE_ROLLER, url, config.HUMAN_ENHET_URL.format(orgnr=orgnr), OFFICIAL_ROLES, resp.sha256)
    facts = extract_role_facts(resp.json(), source, resp.fetched_at, orgnr)
    return from_facts("roles", facts, url, resp.fetched_at, "no active roles registered")


def extract_role_facts(data: dict[str, Any], source: Source, retrieved_at: str, orgnr: str) -> tuple[Fact, ...]:
    roles: list[dict[str, Any]] = []
    for group in data.get("rollegrupper") or []:
        changed = group.get("sistEndret")
        for role in group.get("roller") or []:
            if role.get("avregistrert") or role.get("fratraadt"):
                continue
            holder = _holder(role)
            if holder is None:
                continue
            rtype = role.get("type") or {}
            roles.append({
                "role_code": rtype.get("kode"),
                "role": rtype.get("beskrivelse"),
                "group": (group.get("type") or {}).get("beskrivelse"),
                "holder": holder,
                "group_last_changed": changed,
            })
    if not roles:
        return ()

    guard = {"matched_orgnr": orgnr}
    facts: list[Fact] = []
    latest = max((r["group_last_changed"] for r in roles if r["group_last_changed"]), default=None)
    facts.append(make_fact("roles", roles, source, retrieved_at, json_path="rollegrupper[].roller[]",
                           as_of=latest, label="Active roles", extra_evidence=guard))

    for code, (name, label) in _KEY_ROLES.items():
        holders = [r for r in roles if r["role_code"] == code]
        if holders:
            value = holders[0]["holder"] if len(holders) == 1 else [h["holder"] for h in holders]
            facts.append(make_fact(name, value, source, retrieved_at,
                                   json_path=f"rollegrupper[type.kode={code}]",
                                   as_of=holders[0]["group_last_changed"], label=label, extra_evidence=guard))
    board = [r["holder"] for r in roles if r["role_code"] in ("LEDE", "NEST", "MEDL")]
    if board:
        facts.append(make_fact("board_members", board, source, retrieved_at,
                               json_path="rollegrupper[type.kode=STYR]", as_of=latest,
                               label="Board members", extra_evidence=guard))
    return tuple(facts)


def _holder(role: dict[str, Any]) -> dict[str, Any] | None:
    person = role.get("person")
    if person:
        n = person.get("navn") or {}
        full = " ".join(p for p in (n.get("fornavn"), n.get("mellomnavn"), n.get("etternavn")) if p)
        return {"type": "person", "name": full, "deceased": bool(person.get("erDoed"))}
    entity = role.get("enhet")
    if entity:
        names = entity.get("navn") or []
        return {
            "type": "organization",
            "name": " ".join(names) if isinstance(names, list) else names,
            "orgnr": entity.get("organisasjonsnummer"),
            "deleted": bool(entity.get("erSlettet")),
        }
    return None
