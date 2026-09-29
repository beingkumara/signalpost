"""English names for Norwegian organisation form codes."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

ORG_FORMS_EN = {
    "AS": "private limited company",
    "ASA": "public limited company",
    "ENK": "sole proprietorship",
    "NUF": "Norwegian-registered foreign enterprise",
    "ANS": "general partnership",
    "DA": "partnership with shared liability",
    "KS": "limited partnership",
    "SA": "cooperative",
    "BA": "company with limited liability",
    "STI": "foundation",
    "FLI": "association",
    "BRL": "housing cooperative",
    "ESEK": "condominium owners' association",
    "SAM": "jointly owned property",
    "IKS": "inter-municipal company",
    "KF": "municipal enterprise",
    "FKF": "county municipal enterprise",
    "SF": "state enterprise",
    "STAT": "state body",
    "KOMM": "municipality",
    "FYLK": "county authority",
    "ORGL": "organisational unit of a public body",
    "KIRK": "Church of Norway body",
    "PK": "pension fund",
    "SPA": "savings bank",
    "GFS": "mutual insurance company",
    "PRE": "shipping partnership",
    "OPMV": "separate VAT-registered unit",
    "BEDR": "sub-unit (business establishment)",
    "AAFY": "sub-unit (non-business)",
    "SE": "European company",
    "TVAM": "tax-registered entity",
    "ADOS": "administrative unit of a public body",
    "EOFG": "European economic interest grouping",
    "KBO": "estate in bankruptcy",
    "VPFO": "securities fund",
    "OSLO": "Oslo stock exchange",
    "PERS": "other person",
    "IKJP": "other legal person",
    "UTLA": "foreign entity",
    "BO": "other estate",
}


def org_form_en(code: str | None, fallback: str | None = None) -> str | None:
    if code and code in ORG_FORMS_EN:
        return ORG_FORMS_EN[code]
    return fallback.lower() if fallback else None


def with_article(noun: str) -> str:
    return ("an " if noun[:1].lower() in "aeiou" else "a ") + noun


@lru_cache(maxsize=1)
def _nace() -> dict[str, dict[str, str]]:
    try:
        text = resources.files("signalpost").joinpath("data/nace_en.json").read_text(encoding="utf-8")
        return json.loads(text)["codes"]
    except (OSError, ValueError, KeyError):
        return {}


def industry_en(code: str | None) -> str | None:
    """Official English industry name from Statistics Norway (SN2025, falling back to SN2007)."""
    if not code:
        return None
    entry = _nace().get(code)
    return entry["name"] if entry else None
