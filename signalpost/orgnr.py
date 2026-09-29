"""Norwegian organisation number (organisasjonsnummer) validation."""

from __future__ import annotations

import re

_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
_NON_DIGITS = re.compile(r"[\s.\-]")


class InvalidOrgnr(ValueError):
    """Raised when input is not a valid 9-digit MOD11 organisation number."""


def normalize(raw: str) -> str:
    """Strip spaces, dots, dashes and an optional NO/MVA wrapper."""
    text = raw.strip().upper()
    if text.startswith("NO"):
        text = text[2:]
    if text.endswith("MVA"):
        text = text[:-3]
    return _NON_DIGITS.sub("", text)


def checksum_ok(digits: str) -> bool:
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:8], _WEIGHTS))
    remainder = total % 11
    control = 0 if remainder == 0 else 11 - remainder
    if control == 10:
        return False
    return control == int(digits[8])


def parse(raw: str) -> str:
    """Return a validated 9-digit orgnr or raise InvalidOrgnr."""
    digits = normalize(raw)
    if len(digits) != 9 or not digits.isdigit():
        raise InvalidOrgnr(f"'{raw}' is not a 9-digit organisation number")
    if not checksum_ok(digits):
        raise InvalidOrgnr(f"'{raw}' fails the MOD11 control digit check")
    return digits


def pretty(orgnr: str) -> str:
    """Format as '923 609 016'."""
    return f"{orgnr[0:3]} {orgnr[3:6]} {orgnr[6:9]}"
