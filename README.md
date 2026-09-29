# Signalpost company research agent

Give it a batch of Norwegian organisation numbers. For each one it researches official registers, the company's own website and NAV's official job feed, and returns **exactly one terminal envelope**. Every claim in the envelope carries a source URL, retrieval time, content hash, reporting period where relevant, and an explicit availability state. Profiles are kept current: each run diffs against the previous snapshot and reports typed, material changes while preserving earlier evidence.

## Evaluator command

```bash
python3 -m signalpost run --input companies.jsonl --out out/
```

- `companies.jsonl` is the official batch: one JSON object per line with `organisation_number` (the public universe format). JSON lists, CSV and plain text also work.
- Output in `out/`:
  - `envelopes.jsonl` has one envelope per input, in input order.
  - `run_report.json` has validation checks, section and field coverage, requests, p50/p95 latency, runtime and cost.
  - `report.html` is a self-contained viewer to search, compare and verify profiles on desktop and mobile.
- Exit code 0 means every input produced a valid terminal envelope.

**Install:** none. Python 3.10+ standard library only. There are no third-party runtime dependencies, API keys or secrets. `uv.lock` pins the dev and test tools.

**Useful options:**

| Option | Default | Purpose |
|---|---|---|
| `--expected-count N` | none | Fail the run if the envelope count differs |
| `--budget N` or `SIGNALPOST_REQUEST_BUDGET` | 18 per company + 150 per run | Hard cap on outbound requests |
| `--max-minutes M` or `SIGNALPOST_MAX_MINUTES` | 30 | Stop starting new source checks after M minutes. Envelopes are still written for everyone. |
| `--previous old/envelopes.jsonl` | none | Diff against an earlier run's envelopes |
| `--profiles-dir DIR` (before `run`) | `state/profiles` | Stored snapshots used for refresh diffs |
| `--workers N` | 16 | Parallel companies |
| `--no-history`, `--no-jobs`, `--no-website-discovery` | off | Disable a source lane |

Other commands:

```bash
python3 -m signalpost profile 923609016 --text          # one company, readable summary
python3 -m signalpost sample --count 100 --out data/smoke-companies.jsonl   # needs the public universe file
python3 -m signalpost update                             # refresh stored profiles changed upstream (Brønnøysund change feeds)
```

To build a local test batch, download the public universe first:

```bash
curl -L https://builderr.ai/signalpost-company-universe-2025.jsonl.gz -o data/signalpost-universe.jsonl.gz
```

## What each envelope contains

Each envelope has five sections, and every expected field appears as a claim, found or not.

| Section | Claims |
|---|---|
| Legal identity and brand | name, form, status, addresses, industry, founding date, employees, share capital, purpose, registered contact details, public brand name |
| Annual accounts | period, revenue, operating profit, net profit, equity, assets, debt (company and group), filing history with links to official copies |
| Leadership and workplaces | CEO, board chair, board members, auditor, accountant, all active roles, registered sub-units |
| Website and profiles | verified official website, self-description, contact emails and phones, company-owned social profiles |
| Hiring and activity | NAV job postings (employer org number verified), careers page, dated news on the company site, dated events in official records |

Availability states are `available`, `not_available`, `blocked`, `not_applicable`, `ambiguous` and `failed`, each with a reason. Absence is never turned into zero. See [DATA_SCHEMA.md](DATA_SCHEMA.md).

## Accuracy first

- Lookups use the exact organisation number. The agent never searches the registry by name.
- Every registry, accounts and sub-unit response must echo the requested number, or it is discarded.
- A website is published only if a crawled page shows the org number. The registered name is enough only when the registry itself links the site, through its website field or email domain; that case gets medium confidence.
- Job ads are published only when the ad's employer org number is the company or one of its registered sub-units.
- Financial values are copied verbatim with currency and period. There is no language model and no estimation.

Details are in [AGENT.md](AGENT.md), [SOURCES.md](SOURCES.md), [REFRESH.md](REFRESH.md) and [LIMITATIONS.md](LIMITATIONS.md).

## Models, APIs and cost

| Item | Value |
|---|---|
| Language models | None |
| APIs | Brønnøysundregistrene open data (NLOD 2.0), NAV job vacancy feed (public token), company websites |
| Third-party cost | $0.00 per run |
| Requests | about 7 per company (measured 706 for 100 companies in 5.1 minutes) |

## Submitted profiles

- `submission/profiles-1000/` holds 1,000 company profiles (`envelopes.jsonl.gz`) with their run report and input list. The run took 6,094 requests and 35.9 minutes, at $0.
- `submission/smoke-100/` holds a 100-company run with a browsable `report.html`.

The submission checklist is in [submission/SUBMISSION.md](submission/SUBMISSION.md).

## Development

```bash
uv run --with pytest --with pytest-cov pytest --cov=signalpost
```

Tests run offline against real registry responses saved in `tests/fixtures/`.
