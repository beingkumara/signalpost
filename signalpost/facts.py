"""Fact model: every published value carries its source, date and evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    human_url: str | None = None


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

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
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
) -> Fact:
    evidence: dict[str, Any] = dict(extra_evidence or {})
    if json_path:
        evidence["json_path"] = json_path
    basis = as_of_basis or ("source_field" if as_of else "retrieval_date")
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
    )


def is_present(value: Any) -> bool:
    return value not in (None, "", [], {})
