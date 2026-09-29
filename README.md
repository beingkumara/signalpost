# Signalpost company agent

Give it a Norwegian organisation number. It looks the company up in permitted public sources and returns a profile where **every fact has a source link, a date and evidence that it belongs to that exact org number**. Profiles are stored and kept current: each refresh is diffed against the last one and changes are logged.

## One command to run it

```bash
python3 -m signalpost run --input companies.txt --out out/
```

- `companies.txt` holds one org number per line (`923609016`, `923 609 016` and `NO 923609016 MVA` all work), or a JSON list.
- Org numbers can also be passed directly: `python3 -m signalpost run 923609016 935567645`.
- Output: `out/results.jsonl` (one profile per line, input order), `out/run_report.json` (counts, requests used, duration, cost), and one stored profile per company in `profiles/<orgnr>.json`.

Requirements: Python 3.10+ and nothing else. There are no third-party runtime dependencies and no API keys.

Other commands:

| Command | What it does |
|---|---|
| `python3 -m signalpost profile 923609016 --text` | Profile one company and print the plain-English explanation |
| `python3 -m signalpost show 923609016` | Print the stored explanation and sources |
| `python3 -m signalpost update` | Refresh stored profiles that changed upstream (Brønnøysund change feeds) or went stale |
| `python3 -m signalpost seed --count 1000` | Sample org numbers across Norwegian municipalities |
| `python3 -m signalpost export --out out/all_profiles.jsonl` | Write every stored profile to one file |

Useful flags: `--workers` (default 8), `--max-minutes` (default 40), `--budget` (outbound request cap, default 1900), `--no-website`, `--profiles-dir`.

## Sources

All sources are official and open, and none need an account.

| Source | Endpoint | Facts |
|---|---|---|
| Enhetsregisteret (Brønnøysundregistrene) | `data.brreg.no/enhetsregisteret/api/enheter/{orgnr}`, falls back to `/underenheter/{orgnr}` | name, organisation form, founded and registration dates, addresses, municipality, NACE industry codes, employees, website, phone, email, share capital, stated purpose, sector, VAT and register memberships, former names, bankruptcy, liquidation, forced dissolution, deletion |
| Enhetsregisteret roles | `/enheter/{orgnr}/roller` | CEO, board chair, board members, auditor, accountant, proprietor, all active roles |
| Regnskapsregisteret | `data.brreg.no/regnskapsregisteret/regnskap/{orgnr}` | latest filed accounts: revenue, operating profit, net profit, assets, equity, debt, with currency, period and journal number; group accounts when filed |
| Sub-units | `/underenheter?overordnetEnhet={orgnr}` | number of establishments and the first five with address and employees |
| Company website | only the URL registered in Enhetsregisteret | title, self-description, same-domain emails, social links, used only after an identity check |
| Change feeds | `/oppdateringer/enheter`, `/oppdateringer/roller` | which stored profiles need a refresh |

Robots.txt is honoured for company websites. Role holders' birth dates are dropped on purpose.

## How facts are kept on the right company

1. **Validated input.** The org number must pass the MOD11 control digit check, or nothing is looked up.
2. **Exact-number lookups only.** The agent never searches by name, so it cannot pick a similarly named company.
3. **Identity guard.** Every registry response must echo the requested org number. Accounts and sub-units are filtered on the org number too.
4. **Website identity check.** Website content is published only if the page shows the org number (`orgnr_on_page`, high confidence) or the registered name (`name_on_page`, medium confidence). Otherwise it is skipped with a warning.
5. **No fabricated numbers.** Financial values are copied verbatim from Regnskapsregisteret, with currency and period. Nothing is estimated. There is no language model in the loop.

## Profile format

```jsonc
{
  "orgnr": "923609016",
  "name": "EQUINOR ASA",
  "status": "active",            // active | bankrupt | under_liquidation | under_forced_dissolution | deleted | subunit | not_found | invalid_input | lookup_failed
  "checked_at": "2026-09-29T10:05:07Z",
  "fields": { "name": "EQUINOR ASA", "revenue": {"amount": 67956000000.0, "currency": "USD", "year": "2025"}, ... },
  "facts": [
    {
      "field": "revenue",
      "value": {"amount": 67956000000.0, "currency": "USD", "year": "2025"},
      "label": "Operating revenue",
      "source": {"name": "Brønnøysundregistrene: Regnskapsregisteret",
                 "url": "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
                 "human_url": "https://virksomhet.brreg.no/nb/oppslag/enheter/923609016"},
      "as_of": "2025-12-31", "as_of_basis": "accounting_period_end",
      "retrieved_at": "2026-09-29T10:05:07Z", "first_seen": "2026-09-29T10:05:07Z",
      "evidence": {"matched_orgnr": "923609016", "json_path": "resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter",
                   "journal_number": "2026635024", "period": {"from": "2025-01-01", "to": "2025-12-31"}},
      "confidence": "high"
    }
  ],
  "explanation": { "summary": "EQUINOR ASA (923 609 016), a public limited company, is an active entity ...",
                   "notes": ["..."], "changes_since_last_check": ["Changed: Registered employees (was 21,100, now 21,272)."] },
  "sources_checked": [ {"source": "Regnskapsregisteret", "url": "...", "http_status": 200, "found_data": true} ],
  "revision": 2, "changes_this_run": [...], "change_history": [...], "warnings": []
}
```

`as_of` is the date the source gives for the fact, such as the accounting period end, the share capital date or the date roles last changed. When the source gives no date, `as_of` is the retrieval date and `as_of_basis` says so.

## Keeping profiles current

- Every `run` re-fetches each company and diffs it against the stored profile. `changes_this_run` lists added, changed and removed values, `change_history` keeps the last 200 changes, and `revision` increases only when something changed. `first_seen` shows how long a value has been unchanged.
- If a secondary source fails during a refresh, the previous facts from that source are kept and marked `"stale": true`, instead of being deleted.
- If the core lookup fails, the last good profile is kept and the failure is recorded.
- `update` reads Brønnøysund's entity and role change feeds since the last sync and refreshes only the stored companies that changed, then those not checked for `--stale-days` (default 30). It stays inside the request budget and defers the rest.

## Model, API and cost details

| Item | Value |
|---|---|
| Language model | None. Explanations are deterministic templates over sourced facts. |
| External APIs | Brønnøysundregistrene open data (free, no key), company websites |
| Paid API cost | **$0.00 per run** |
| Requests per company | 3 to 7 (entity, roles, accounts, sub-units, robots.txt, homepage). A deleted or sub-unit record takes 1 or 2. |
| 100 companies | about 500 to 650 outbound requests, well below 2,000. The `--budget` cap stops new work before the limit. |
| Runtime for 100 companies | a few minutes with 8 workers. `--max-minutes` stops scheduling new companies after 40 minutes. |

## Development

```bash
uv run --with pytest --with pytest-cov pytest --cov=signalpost
```

Tests run offline against real registry responses saved in `tests/fixtures/`.
