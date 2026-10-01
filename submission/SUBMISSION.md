# Signalpost submission

This follows the "What to Submit" list in the challenge statement.

| Required item | Answer |
|---|---|
| At least 1,000 company profiles | `submission/profiles-1000/envelopes.jsonl.gz`: 1,000 profiles, one per company, from the public company list (seed 20260825). `submission/smoke-100/` adds a 100-company run with a browsable `report.html`. |
| Your repository link | *(add after pushing)* |
| Exact commit hash | *(the commit that contains this file: `git log -1 --format=%H`)* |
| One command to run it | `python3 -m signalpost run --input companies.jsonl --out out/` |
| Model/API details | Free public sources by default: Brønnøysundregistrene open data (Enhetsregisteret, roles, change feeds, Regnskapsregisteret; NLOD 2.0), NAV's official job vacancy feed via NAV's published public token, and company-owned websites (robots.txt respected). Web search for website discovery through Tavily's keyless Search mode (no account, no cost). Optional, read from environment variables: keyed search via Tavily, Exa, Linkup or Firecrawl (`TAVILY_API_KEY` etc.), and Claude Haiku 4.5 (`ANTHROPIC_API_KEY`, model `claude-haiku-4-5-20251001`) for validated overviews. |
| Expected run costs | $0.00 without keys. With keys: at most about $1.20 for search (150-search cap per run) plus at most $1.00 for overviews (hard cap), about $2.20 per daily run of 100 companies. |

## Fit with the daily test limits

The daily test is 100 companies, with limits of 45 minutes, 2,000 outbound requests and $10 in external API costs.

| Limit | Agent default | Measured on 100 companies |
|---|---|---|
| 45 minutes | Stops starting new source checks after 30 minutes (`SIGNALPOST_MAX_MINUTES`) | 4.7 minutes |
| 2,000 outbound requests | Hard cap of 18 per company + 150 per run = 1,950 (`SIGNALPOST_REQUEST_BUDGET`) | 1,257 requests, including 24 keyless searches |
| $10 external API cost | Search capped at 150 per run; overviews capped at $1 (`SIGNALPOST_LLM_MAX_USD`) | $0.00 without keys; ≤ $2.20 with keys |

Every input company returns exactly one result, even when a source is blocked or a limit is reached.

## How the agent covers the scoring areas

| Scoring area | What the agent does |
|---|---|
| Find useful information | Registry identity, accounts and filing history, roles, workplaces, verified website, contacts, social profiles, NAV job postings, and dated events |
| Match it correctly with evidence | Exact org-number lookups only. Websites and jobs are published only after org-number proof. Every claim has a source URL, retrieval time, content hash and extraction method. |
| Update correctly | Every profile lists the changes Brønnøysund recorded in the last 12 months, field by field and dated, even on a first run. Re-runs also diff against the stored profile: changes are typed and flagged as material, with evidence for both the old and new values. Identical data gives zero changes. |
| Give useful explanations | A plain-English summary, plus key points that cite the claims they rest on, and a list of what was not found and why |
| Make it easy to use | One command, no install step, and a searchable report page (`report.html`) that works on desktop and mobile |
