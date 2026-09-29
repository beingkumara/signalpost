"""Fact model: every published value carries its source, date, content hash and evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Source classes used in evidence records (see DATA_SCHEMA.md).
OFFICIAL_REGISTRY = "official_registry"
OFFICIAL_ROLES = "official_roles"
OFFICIAL_ACCOUNTS = "official_annual_accounts"
OFFICIAL_ACCOUNT_COPIES = "official_annual_account_copies"
COMPANY_OWNED = "company_owned"
PUBLIC_JOBS = "public_job_board"


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    human_url: str | None = None
    source_class: str = OFFICIAL_REGISTRY
    content_sha256: str | None = None


@dataclass(frozen=True)
class Fact:
    field: str
    value: Any
    source: Source
    retrieved_at: str
    as_of: str | None = None
    as_of_basis: str = "retrieval_date"
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: str = "high"
    label: str | None = None
    extraction_method: str = "json_path"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = {k: v for k, v in data["source"].items() if v is not None}
        return {k: v for k, v in data.items() if v is not None}


def make_fact(
    field_name: str,
    value: Any,
    source: Source,
    retrieved_at: str,
    *,
    json_path: str | None = None,
    as_of: str | None = None,
    as_of_basis: str | None = None,
    confidence: str = "high",
    label: str | None = None,
    extra_evidence: dict[str, Any] | None = None,
    claim_span: str | None = None,
    extraction_method: str | None = None,
) -> Fact:
    evidence: dict[str, Any] = dict(extra_evidence or {})
    if json_path:
        evidence["json_path"] = json_path
    if claim_span:
        evidence["claim_span"] = claim_span[:400]
    basis = as_of_basis or ("source_field" if as_of else "retrieval_date")
    method = extraction_method or ("json_path" if json_path else "html_parse")
    return Fact(
        field=field_name,
        value=value,
        source=source,
        retrieved_at=retrieved_at,
        as_of=as_of or retrieved_at[:10],
        as_of_basis=basis,
        evidence=evidence,
        confidence=confidence,
        label=label,
        extraction_method=method,
    )


def is_present(value: Any) -> bool:
    return value not in (None, "", [], {})
