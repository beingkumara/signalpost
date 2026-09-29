# Signalpost submission

| Field | Value |
|---|---|
| Agent name | Signalpost evidence agent |
| Repository URL | *(add after pushing)* |
| Exact commit hash | *(the commit that contains this file: `git log -1 --format=%H`)* |
| 100-company smoke-test result | `submission/smoke-100/`: `envelopes.jsonl`, `run_report.json`, `report.html` |
| One-command run instruction | `python3 -m signalpost run --input companies.jsonl --out out/` |
| Install step | None. Python 3.10+ standard library only; no third-party runtime packages. |
| Models / APIs / licences | No language model. Brønnøysundregistrene open data (NLOD 2.0); NAV job vacancy feed via NAV's published public token; company-owned websites (robots.txt respected). Code is the builder's own. |
| Expected cost per official run | $0.00 in third-party API costs |
| Contact for results | *(add)* |

## Measured runs

| Run | Companies | Envelopes | Requests | Runtime | Cost |
|---|---|---|---|---|---|
| Smoke test (universe sample, seed 20260823) | 100 | 100, validation passed | 706 | 5.1 min | $0 |
| Local scale test (seed 20260825) | 1,000 | 1,000, validation passed | 6,018 | 36.8 min with filing history for all 1,000 (the default 30-minute budget caps this lane) | $0 |

Defaults are 19 requests per company, overridable with `SIGNALPOST_REQUEST_BUDGET`, and a 30-minute time budget, overridable with `SIGNALPOST_MAX_MINUTES`. Every input returns exactly one terminal envelope, even when a source is blocked or the budget runs out.
