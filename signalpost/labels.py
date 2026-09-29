"""English names for Norwegian organisation form codes."""

from __future__ import annotations

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
