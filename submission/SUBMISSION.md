# Signalpost submission

| Required item | Answer |
|---|---|
| Company profiles | 1,000 profiles in `profiles/<orgnr>.json`, also bundled as `submission/profiles.jsonl.gz` |
| Repository link | *(add after pushing)* |
| Exact commit hash | *(the hash of the commit that contains this file, see `git log -1`)* |
| One command to run it | `python3 -m signalpost run --input companies.txt --out out/` |
| Model / API details | No language model. Public Brønnøysundregistrene open-data APIs (Enhetsregisteret, roles, Regnskapsregisteret, sub-units, change feeds) with no key, plus the company's own registered website after an identity check. |
| Expected run costs | $0.00 in external API costs per daily run. |

## Measured runs

| Run | Companies | Outbound requests | Duration | Cost |
|---|---|---|---|---|
| Daily-test settings, 8 workers | 100 | 424 | 81 s | $0 |
| Bulk build, 12 workers | 1,000 | 4,169 | 466 s | $0 |

Daily limits are 2,000 requests, 45 minutes and $10. The agent stops scheduling new companies at a default budget of 1,900 requests or after 40 minutes.

Requirements: Python 3.10 or newer, no third-party packages.
