# Research and abstention policy

## Order of work per company

1. **Identity anchor.** Entity records are fetched 100 at a time with the registry's batch lookup (`organisasjonsnummer=a,b,...`). Numbers missing from the batch, such as deleted units and sub-units, fall back to a single lookup. The organisation number is the only key.
2. **Core official sources.** Annual accounts and roles are fetched for every main unit. Brønnøysund's change feeds are then read for the company's last 12 months of recorded changes, field by field. The request budget always reserves two requests per company not yet started, so core lookups are never starved by optional work.
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
| Website from the registry's website field | A crawled page shows the org number (high confidence). Otherwise the registered name must meet three conditions (medium confidence): it is distinctive, it appears in the title, site name, structured data or copyright line, and the site is tied to the company by that title or its domain. |
| Website from the registry's email domain | Same as above: this is also an official registry link |
| Website from a guessed domain, job-ad homepage or web-search result | A crawled page (home, contact, about, privacy) shows the org number. Directories, registries, media and social platforms are never candidates. |
| Any website (ownership checks, run first) | The URL is not a directory, registry or social-network page. A redirect to another domain must land on that site's home page, not a listing on a shared platform (e.g. sjekkpunkt.no/bilverksted/x). A name-only match fails when the site states a different org number: that is a parent group's or sibling's site. |
| Job posting (NAV) | The ad's embedded employer org number equals the company or one of its sub-units. The link is always the single ad on arbeidsplassen.nav.no. |
| Social profiles | Linked from the verified website, pointing at an account (not a post, video, share link or personal LinkedIn page), and the handle contains the company's name, a distinctive word of it or the site's domain label. Theme, agency and parent-group accounts (facebook.com/Wix) fail. A YouTube channel ID counts only from the company's own schema.org Organization data. |
| News | A real post with a publish date: an `<article>` with a headline linking to the post, a post page with `article:published_time`, or NewsArticle/BlogPosting markup. Date-only text, menu labels ("Se flere nyheter"), contact, legal, about and careers pages, home pages, "updated"/"modified" stamps and future dates are skipped. |
| Careers page, website job postings, recruitment platforms | Careers links need a whole word ("karriere", "ledige stillinger", "jobb"), so "utstillinger" or a news story about a job do not count. Website JobPosting ads must be single ads (own URL), unexpired, and not hiring for another organisation. Platform links count only for the company's own account (acme.teamtailor.com), never NAV/FINN search links or other employers' ads. |
| Emails, phones | They sit on a verified company-owned website. Emails must be on the site's own domain. |

When identity is uncertain the agent returns `ambiguous` or `not_available` and publishes nothing from that source. Search results and name similarity only generate candidates. They are never evidence.

## Budgets

| Budget | Default |
|---|---|
| Request budget | 18 per company plus 150 per run for the NAV feed (1,950 for 100 companies, inside the published 2,000), or `SIGNALPOST_REQUEST_BUDGET` |
| Time budget | 30 minutes, or `SIGNALPOST_MAX_MINUTES`. After the deadline no new source checks start. Unchecked sources are reported as `blocked` with the reason. |

Every input still gets an envelope, whatever happens.

## Language model (optional)

By default, explanations are templates over sourced claims, and each statement lists the claim IDs it rests on.

With `ANTHROPIC_API_KEY` set, Claude Haiku also writes a 3 to 5 sentence overview from the available claims only. The output is validated before use:
- every sentence must cite existing claims;
- every number in a sentence must appear in the claims it cites.

Failing sentences are dropped, and the model never decides identity. Spend is capped by `SIGNALPOST_LLM_MAX_USD`, $1 by default.
