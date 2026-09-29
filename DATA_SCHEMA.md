# Envelope schema

One JSON object per input line in `envelopes.jsonl`.

```jsonc
{
  "organisation_number": "923609016",
  "name": "EQUINOR ASA",
  "schema_version": "2.0",
  "run": {"run_id": "...", "started_at": "...", "completed_at": "...", "terminal_status": "completed"},
  "state": "available",                 // entity-level: available | not_available | failed
  "company_status": "active",           // active | bankrupt | under_liquidation | under_forced_dissolution | deleted | subunit | not_found | invalid_input | lookup_failed
  "sections": {
    "legal_identity_and_brand": {"state": "available", "available_claims": 28, "unavailable_states": [], "claim_ids": ["c1", "..."]},
    "annual_accounts": {}, "leadership_and_workplaces": {}, "website_and_profiles": {}, "hiring_and_activity": {}
  },
  "claims": [
    {"id": "c31", "field": "revenue", "section": "annual_accounts", "label": "Operating revenue",
     "value": {"amount": 67956000000.0, "currency": "USD", "year": "2025"},
     "availability": "available", "confidence": 0.97, "evidence_ids": ["e31"],
     "as_of": "2025-12-31", "as_of_basis": "accounting_period_end",
     "reporting_period": {"from": "2025-01-01", "to": "2025-12-31"}, "first_seen": "2026-09-29T11:36:41Z"},
    {"id": "c61", "field": "job_postings", "section": "hiring_and_activity", "label": "Active job postings",
     "value": null, "availability": "not_available", "reason": "no active ads on arbeidsplassen.nav.no for this name",
     "evidence_ids": []}
  ],
  "evidence": [
    {"id": "e31", "source_name": "Brønnøysundregistrene: Regnskapsregisteret",
     "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
     "human_url": "https://virksomhet.brreg.no/nb/oppslag/enheter/923609016",
     "source_class": "official_annual_accounts", "retrieved_at": "2026-09-29T11:36:41Z",
     "content_sha256": "…", "extraction_method": "json_path",
     "json_path": "resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter",
     "claim_span": null, "identity_proof": {"matched_orgnr": "923609016"}}
  ],
  "availability": {"registry": {"state": "available"}, "jobs": {"state": "blocked", "reason": "HTTP 429"}},
  "summary": {"summary": "…", "notes": ["…"], "statements": [{"topic": "financials", "text": "…", "claim_ids": ["c30", "c31"]}]},
  "changes": [{"field": "ceo", "change": "changed", "change_type": "role_change", "material": true, "old": {}, "new": {},
               "detected_at": "…", "previous_evidence": {}, "current_evidence": {}}],
  "refresh": {"checked_at": "…", "previous_checked_at": "…", "first_checked_at": "…", "revision": 2,
              "material_changes": 1, "prior_evidence_preserved": 5},
  "errors": ["jobs: HTTP 429"],
  "operations": {"requests": 8, "runtime_ms": 10454, "third_party_cost_usd": 0.0}
}
```

## Availability states

| State | Meaning |
|---|---|
| `available` | Found, with evidence |
| `not_available` | Checked; the source has nothing for this company |
| `not_applicable` | The question does not apply, for example accounts for a deleted unit or a board for a sole proprietorship |
| `blocked` | robots.txt disallowed it, the source rate-limited or refused it, or the run's time or request budget ran out first |
| `ambiguous` | The registry-listed website shows neither the org number nor the name, so nothing is published |
| `failed` | Network, TLS or server error |

## Evidence fields

- Newer fields:
  - `official_changes` lists dated change events from Brønnøysund's change feeds, with the changed fields and a `material` flag.
  - `website_job_postings` and `careers_platform` come from verified company sites.
  - `industry_codes[].description_en` gives the official English industry name.
- `source_class` is one of `official_registry`, `official_roles`, `official_annual_accounts`, `official_annual_account_copies`, `company_owned` or `public_job_board`.
- `content_sha256` is the SHA-256 of the exact response body. For batch registry lookups it is the hash of the canonical JSON record, and `retrieved_via` names the batch.
- `extraction_method` is one of `json_path`, `html_title`, `meta_description`, `meta_og`, `mailto_links`, `tel_links`, `outbound_links`, `time_tags`, `identity_gate`, `embedded_json`, `link_classifier` or `derived_from_official_fields`.
- `claim_span` is the text on the page that proves the claim, such as the org-number span used for website identity.
