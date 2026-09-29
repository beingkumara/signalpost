"""Module results: every source reports an explicit availability state, never a silent gap."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from signalpost.facts import Fact

AVAILABLE = "available"
NOT_AVAILABLE = "not_available"
BLOCKED = "blocked"
NOT_APPLICABLE = "not_applicable"
AMBIGUOUS = "ambiguous"
FAILED = "failed"
STATES = (AVAILABLE, NOT_AVAILABLE, BLOCKED, NOT_APPLICABLE, AMBIGUOUS, FAILED)


@dataclass(frozen=True)
class ModuleResult:
    module: str
    state: str
    facts: tuple[Fact, ...] = ()
    reason: str | None = None
    source_url: str | None = None
    checked_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"invalid module state {self.state!r}")

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {"module": self.module, "state": self.state, "facts": len(self.facts)}
        for key in ("reason", "source_url", "checked_at"):
            value = getattr(self, key)
            if value:
                row[key] = value
        return row


def from_facts(module: str, facts: tuple[Fact, ...], url: str, at: str, empty_reason: str) -> ModuleResult:
    if facts:
        return ModuleResult(module, AVAILABLE, facts, None, url, at)
    return ModuleResult(module, NOT_AVAILABLE, (), empty_reason, url, at)


def skipped(module: str, state: str, reason: str) -> ModuleResult:
    return ModuleResult(module, state, (), reason)
