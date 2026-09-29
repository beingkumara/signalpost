"""Read an official batch: JSONL rows with `organisation_number`, a JSON list, CSV or plain text."""

from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path
from typing import Any

KEYS = ("organisation_number", "organization_number", "orgnr", "organisasjonsnummer", "org")


def read_batch(path: Path) -> list[str]:
    raw = path.read_bytes()
    text = (gzip.decompress(raw) if path.suffix == ".gz" else raw).decode("utf-8-sig")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        return [_value(v) for v in json.loads(stripped)]
    if stripped.startswith("{") and "\n" not in stripped:
        body = json.loads(stripped)
        if any(k in body for k in KEYS):
            return [_value(body)]
        return [_value(v) for v in body.get("organisation_numbers", body.get("companies", []))]
    if stripped.startswith("{"):
        return [_value(json.loads(line)) for line in stripped.splitlines() if line.strip()]
    first = stripped.splitlines()[0].lower()
    if "," in first and any(k in first for k in KEYS):
        reader = csv.DictReader(io.StringIO(stripped))
        return [_value(row) for row in reader]
    return [line.split(",")[0].strip() for line in stripped.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def _value(item: Any) -> str:
    if isinstance(item, dict):
        for key in KEYS:
            if item.get(key) not in (None, ""):
                return str(item[key]).strip()
        return ""
    return str(item).strip()


def unique_in_order(items: list[str]) -> tuple[list[str], list[str]]:
    seen: set[str] = set()
    unique: list[str] = []
    duplicates: list[str] = []
    for item in items:
        key = "".join(ch for ch in item if ch.isdigit()) or item
        if key in seen:
            duplicates.append(item)
            continue
        seen.add(key)
        unique.append(item)
    return unique, duplicates
