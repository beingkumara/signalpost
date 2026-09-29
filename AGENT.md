# Research and abstention policy

## Order of work per company

1. **Identity anchor.** Entity records are fetched 100 at a time with the registry's batch lookup (`organisasjonsnummer=a,b,...`). Numbers missing from the batch, such as deleted units and sub-units, fall back to a single lookup. The organisation number is the only key.
2. **Core official sources.** Annual accounts and roles are fetched for every main unit. The request budget always reserves two requests per company not yet started, so core lookups are never starved by optional work.
3. **Optional sources.** These run in priority order, each only if budget and time remain:
   1. Sub-units (registered workplaces).
   2. The website: the registry-listed site, or discovery when none is listed.
4. **Jobs lane.** NAV's official job feed is read once per run in a background thread (ads changed in the last 45 days). Employer names are matched locally. Only candidate ads are opened, and the employer org number is verified.
5. **Filing history.** A separate paced lane fetches the years with official account copies (about 30 requests per minute upstream).
6. **Derived dated events.** Registration, articles, share capital, name changes, bankruptcy and role-group changes are taken from official fields. They need no extra requests.

## Publication gates

| Claim type | Published when |
|---|---|
| Registry, roles, accounts, sub-units | The response echoes the requested org number |
| Website from the registry's website field | A crawled page shows the org number (high confidence) or the registered name (medium) |
| Website from the registry's email domain | Same as above: this is also an official registry link |
| Website from a guessed domain or job-ad homepage | A crawled page (home, contact, about) shows the org number |
| Job posting | The ad's embedded employer org number equals the company or one of its sub-units |
| Social profiles, emails, phones, news | They sit on a verified company-owned website. Emails must be on the site's own domain. |

When identity is uncertain the agent returns `ambiguous` or `not_available` and publishes nothing from that source. Search results and name similarity only generate candidates. They are never evidence.

## Budgets

| Budget | Default |
|---|---|
| Request budget | 19 per company (the published limit was 2,000 per 100 companies), or `SIGNALPOST_REQUEST_BUDGET` |
| Time budget | 30 minutes, or `SIGNALPOST_MAX_MINUTES`. After the deadline no new source checks start. Unchecked sources are reported as `blocked` with the reason. |

Every input still gets an envelope, whatever happens.

## No language model

Explanations are templates over sourced claims. Each synthesis statement lists the claim IDs it rests on.
