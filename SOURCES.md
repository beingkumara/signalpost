# Sources, rights and outbound policy

| Source | Access | Rights and terms | Use |
|---|---|---|---|
| Brønnøysundregistrene Enhetsregisteret API | Open, no key | NLOD 2.0 | Identity, status, roles, sub-units, change feeds |
| Enhetsregisteret change feeds (`/oppdateringer/enheter`, `/oppdateringer/roller`) | Open, no key | NLOD 2.0 | The last 12 months of recorded changes per company |
| Statistics Norway KLASS (industry classification, English) | Bundled file, no request at run time | CC BY 4.0 | English industry names |
| Regnskapsregisteret API | Open, no key | NLOD 2.0 | Latest accounts; filing-year index, paced to stay under about 30 requests per minute |
| Company websites | Only the registry-listed site, the registry email domain, or candidates that pass the org-number gate | robots.txt checked per host before any page; at most 3 pages per site | Brand, description, contacts, social links, careers page, dated news |
| NAV job vacancy feed (`pam-stilling-feed.nav.no`) | NAV's official feed API with the public token NAV publishes at `/api/publicToken` | Read once per run (ads changed in the last 45 days), paced at 4 requests per second; after HTTP 429 the feed is left alone for 10 minutes and jobs are reported `blocked` | Job postings, published only when the ad's employer org number matches |

| Web search: Tavily keyless mode (default, no account) |  `X-Tavily-Access-Mode: keyless` header on `api.tavily.com/search`, as documented in Tavily's agent-setup guide | Tavily terms; rate limited. On HTTP 429 the provider is switched off for the run. | Same use as below, at no cost |
| Web search: Tavily, Exa, Linkup, Firecrawl (optional) | Official APIs with the operator's own key from environment variables | Provider terms. Results are kept in memory only and used as candidates; they are never stored or cited as evidence. | Candidate websites for companies with staff or meaningful revenue and no registry website |
| Anthropic Messages API (optional) | `ANTHROPIC_API_KEY` | Anthropic commercial terms | English overview written only from the envelope's claims, then validated |

**Not used:**
- LinkedIn, Meta, Glassdoor, Indeed and Google are not scraped. Social profiles are only the links a verified company website publishes.
- Brønnøysund announcement pages (`w2.brreg.no/kunngjoring`) are not used, because robots.txt disallows them.
- Search engines are not used.

## Secrets

None are required. Optional API keys are read from environment variables only. They are never written to logs, envelopes or reports. NAV's public feed token is fetched at run time from NAV's own endpoint. Role holders' birth dates are never stored. Role holders' birth dates are dropped before storage.

## Safe URL handling

Untrusted URLs are company websites, links found on them, and redirect targets. Each one is checked before it is fetched, and again on every redirect ([safe_url.py](signalpost/safe_url.py)):

- The scheme must be http or https, on the default ports.
- The URL may not contain credentials.
- The host must be a public DNS name. Every address it resolves to must be globally routable, which rules out loopback, private networks, link-local and cloud metadata addresses.
- A response is capped at 1.5 MB, and a fetch times out after 8 seconds.

## Caches

No external caches are used. Profiles in `state/profiles/` are the agent's own previous snapshots, used for refresh diffs.
