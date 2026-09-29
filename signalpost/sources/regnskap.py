"""Annual accounts from Regnskapsregisteret.

Values are copied verbatim with currency and period. Nothing is estimated or derived,
so a financial figure is either sourced or absent.
"""

from __future__ import annotations

from typing import Any

from signalpost import config
from signalpost.facts import OFFICIAL_ACCOUNT_COPIES, OFFICIAL_ACCOUNTS, Fact, Source, make_fact
from signalpost.http_client import HttpClient
from signalpost.modules import FAILED, ModuleResult, from_facts

FILING_YEARS_URL = config.BRREG_BASE + "/regnskapsregisteret/regnskap/aarsregnskap/kopi/{orgnr}/aar"
FILING_COPY_URL = config.BRREG_BASE + "/regnskapsregisteret/regnskap/aarsregnskap/kopi/{orgnr}/{year}"

# (field, path through the accounts JSON, label)
_FIGURES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("revenue", ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
     "Operating revenue"),
    ("operating_expenses", ("resultatregnskapResultat", "driftsresultat", "driftskostnad", "sumDriftskostnad"),
     "Operating expenses"),
    ("operating_profit", ("resultatregnskapResultat", "driftsresultat", "driftsresultat"), "Operating profit"),
    ("net_financial_items", ("resultatregnskapResultat", "finansresultat", "nettoFinans"),
     "Net financial items"),
    ("profit_before_tax", ("resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad"),
     "Profit before tax"),
    ("net_profit", ("resultatregnskapResultat", "aarsresultat"), "Net profit (annual result)"),
    ("total_assets", ("eiendeler", "sumEiendeler"), "Total assets"),
    ("fixed_assets", ("eiendeler", "anleggsmidler", "sumAnleggsmidler"), "Fixed assets"),
    ("current_assets", ("eiendeler", "omloepsmidler", "sumOmloepsmidler"), "Current assets"),
    ("equity", ("egenkapitalGjeld", "egenkapital", "sumEgenkapital"), "Equity"),
    ("total_debt", ("egenkapitalGjeld", "gjeldOversikt", "sumGjeld"), "Total debt"),
    ("short_term_debt", ("egenkapitalGjeld", "gjeldOversikt", "kortsiktigGjeld", "sumKortsiktigGjeld"),
     "Short-term debt"),
    ("long_term_debt", ("egenkapitalGjeld", "gjeldOversikt", "langsiktigGjeld", "sumLangsiktigGjeld"),
     "Long-term debt"),
)


def fetch_accounts(client: HttpClient, orgnr: str) -> ModuleResult:
    url = config.REGNSKAP_URL.format(orgnr=orgnr)
    resp = client.get(url)
    if resp.status in (404, 204) or (resp.status == 200 and not resp.body.strip()):
        return from_facts("annual_accounts", (), url, resp.fetched_at, "Regnskapsregisteret returned no accounts")
    if resp.status != 200:
        return ModuleResult("annual_accounts", FAILED, (), f"HTTP {resp.status}", url, resp.fetched_at)
    human = config.HUMAN_ENHET_URL.format(orgnr=orgnr)
    source = Source(config.SOURCE_REGNSKAP, url, human, OFFICIAL_ACCOUNTS, resp.sha256)
    facts = extract_account_facts(resp.json(), source, resp.fetched_at, orgnr)
    return from_facts("annual_accounts", facts, url, resp.fetched_at, "no accounts for this org number")


def fetch_filing_years(client: HttpClient, orgnr: str) -> ModuleResult:
    """Years with an official annual-account copy (history). Rate limited upstream to ~30/minute."""
    url = FILING_YEARS_URL.format(orgnr=orgnr)
    resp = client.get(url, retries=1)
    if resp.status == 404:
        return from_facts("filing_history", (), url, resp.fetched_at, "no filed account copies")
    if resp.status != 200:
        return ModuleResult("filing_history", FAILED, (), f"HTTP {resp.status}", url, resp.fetched_at)
    years = sorted({str(y) for y in resp.json() if str(y).isdigit()}, reverse=True)
    if not years:
        return from_facts("filing_history", (), url, resp.fetched_at, "no filed account copies")
    source = Source(config.SOURCE_REGNSKAP, url, config.HUMAN_ENHET_URL.format(orgnr=orgnr),
                    OFFICIAL_ACCOUNT_COPIES, resp.sha256)
    value = [{"year": y, "copy_url": FILING_COPY_URL.format(orgnr=orgnr, year=y)} for y in years]
    fact = make_fact("filing_history", value, source, resp.fetched_at, json_path="$[*]", as_of=f"{years[0]}-12-31",
                     as_of_basis="latest_filing_year", label="Filed annual accounts (years with official copies)",
                     extra_evidence={"matched_orgnr": orgnr, "years": len(years)})
    return from_facts("filing_history", (fact,), url, resp.fetched_at, "")


def extract_account_facts(data: Any, source: Source, retrieved_at: str, orgnr: str) -> tuple[Fact, ...]:
    statements = [s for s in (data if isinstance(data, list) else [data]) if isinstance(s, dict)]
    statements = [s for s in statements if (s.get("virksomhet") or {}).get("organisasjonsnummer") == orgnr]
    if not statements:
        return ()
    facts: list[Fact] = []
    for statement in sorted(statements, key=_sort_key):
        facts.extend(_statement_facts(statement, source, retrieved_at, orgnr))
    return tuple(facts)


def _sort_key(statement: dict[str, Any]) -> tuple[int, str]:
    # Company accounts first, group (KONSERN) accounts after.
    kind = statement.get("regnskapstype") or ""
    return (0 if kind == "SELSKAP" else 1, kind)


def _statement_facts(statement: dict[str, Any], source: Source, retrieved_at: str, orgnr: str) -> list[Fact]:
    period = statement.get("regnskapsperiode") or {}
    period_end = period.get("tilDato")
    kind = statement.get("regnskapstype") or "SELSKAP"
    prefix = "" if kind == "SELSKAP" else "group_"
    currency = statement.get("valuta") or "NOK"
    evidence = {
        "matched_orgnr": orgnr,
        "journal_number": statement.get("journalnr"),
        "accounts_type": kind,
        "period": {"from": period.get("fraDato"), "to": period_end},
    }

    def fact(name: str, value: Any, path: str, label: str) -> Fact:
        return make_fact(prefix + name, value, source, retrieved_at, json_path=path, as_of=period_end,
                         as_of_basis="accounting_period_end", label=label, extra_evidence=evidence)

    audit = statement.get("revisjon") or {}
    facts = [fact("accounts_period", {"from": period.get("fraDato"), "to": period_end,
                                      "year": (period_end or "")[:4] or None, "currency": currency,
                                      "type": kind, "audited": not audit.get("ikkeRevidertAarsregnskap", False),
                                      "audit_opt_out": bool(audit.get("fravalgRevisjon"))},
                  "regnskapsperiode", "Accounting period")]
    for name, path, label in _FIGURES:
        value = _dig(statement, path)
        if isinstance(value, (int, float)):
            facts.append(fact(name, {"amount": value, "currency": currency, "year": (period_end or "")[:4]},
                              ".".join(path), label))
    return facts


def _dig(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node
