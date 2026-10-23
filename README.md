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

**Install:** none. Python 3.10+ standard library only, with no third-party runtime dependencies. `uv.lock` pins the dev and test tools.

**Web search needs no key.** Tavily's keyless Search mode is used by default as a free, rate-limited fallback (turn it off with `SIGNALPOST_SEARCH_KEYLESS=0`). **Optional API keys** are read from environment variables or a git-ignored `.env` file (see `.env.example`). Without them the agent still costs $0.

| Variable | Adds | Cost per 100-company run |
|---|---|---|
| `TAVILY_API_KEY`, `EXA_API_KEY`, `LINKUP_API_KEY` or `FIRECRAWL_API_KEY` | Web search to find websites the registry does not list. Results are only candidates; a site is published only if the agent finds the org number on it. | ≤ $1.20 (at most 150 searches); $0 within free tiers |
| `ANTHROPIC_API_KEY` | A short English overview per company, written by Claude Haiku from the sourced claims. Every sentence cites claim IDs, and any sentence with a number not in its cited claims is dropped. | ≤ $1.00 (hard cap) |

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
| Hiring and activity | NAV job postings (employer org number verified), job postings and recruitment platforms on the company site, careers page, dated news, dated events in official records, and changes Brønnøysund recorded in the last 12 months (field by field) |

Availability states are `available`, `not_available`, `blocked`, `not_applicable`, `ambiguous` and `failed`, each with a reason. Absence is never turned into zero. See [DATA_SCHEMA.md](DATA_SCHEMA.md).

## Accuracy first

- Lookups use the exact organisation number. The agent never searches the registry by name.
- Every registry, accounts and sub-unit response must echo the requested number, or it is discarded.
- A website is published only if a crawled page shows the org number. The registered name is enough only when the registry itself links the site, through its website field or email domain. The name must also be distinctive, appear in the site's title, site name, structured data or copyright line, and be tied to the company by the title or domain. That case gets medium confidence.
- A registry, directory or social-network URL is never the website, a redirect must land on a site's home page, and a site that states another org number but not this one is treated as a parent group or brand site.
- Social profiles are published only when the handle matches the company (not the parent group, web agency or theme). News must be real, dated posts. Careers pages, website job ads and recruitment platforms must belong to this company.
- Job ads are published only when the ad's employer org number is the company or one of its registered sub-units.
- Financial values are copied verbatim with currency and period. There is no language model and no estimation.

Details are in [AGENT.md](AGENT.md), [SOURCES.md](SOURCES.md), [REFRESH.md](REFRESH.md) and [LIMITATIONS.md](LIMITATIONS.md).

## Models, APIs and cost

| Item | Value |
|---|---|
| Language models | Optional: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) for overviews, only when `ANTHROPIC_API_KEY` is set |
| APIs | Brønnøysundregistrene open data (NLOD 2.0), NAV job vacancy feed (public token), company websites; optional Tavily, Exa, Linkup or Firecrawl search and the Anthropic API |
| Third-party cost | $0.00 without keys; at most about $2.20 per 100-company run with search and overviews enabled |
| Requests | about 11 per company (measured 1,154 for 100 companies in 4.8 minutes) |

## Submitted profiles

- `submission/profiles-1000/` holds 1,000 company profiles (`envelopes.jsonl.gz`) with their run report and input list. The run took 9,486 requests and 36.6 minutes, at $0.
- `submission/smoke-100/` holds a 100-company run with a browsable `report.html`.

The submission checklist is in [submission/SUBMISSION.md](submission/SUBMISSION.md).

## Development

```bash
uv run --with pytest --with pytest-cov pytest --cov=signalpost
```

Tests run offline against real registry responses saved in `tests/fixtures/`.
