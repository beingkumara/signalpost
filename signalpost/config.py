"""Central constants. Everything tunable lives here, not in the modules."""

from __future__ import annotations

USER_AGENT = "SignalpostAgent/1.0 (+https://github.com/; company-profile research bot)"

BRREG_BASE = "https://data.brreg.no"
ENHET_URL = BRREG_BASE + "/enhetsregisteret/api/enheter/{orgnr}"
UNDERENHET_URL = BRREG_BASE + "/enhetsregisteret/api/underenheter/{orgnr}"
ROLLER_URL = BRREG_BASE + "/enhetsregisteret/api/enheter/{orgnr}/roller"
REGNSKAP_URL = BRREG_BASE + "/regnskapsregisteret/regnskap/{orgnr}"
UNDERENHETER_SEARCH_URL = BRREG_BASE + "/enhetsregisteret/api/underenheter?overordnetEnhet={orgnr}&size={size}"
ENHET_UPDATES_URL = BRREG_BASE + "/enhetsregisteret/api/oppdateringer/enheter"
ROLLE_UPDATES_URL = BRREG_BASE + "/enhetsregisteret/api/oppdateringer/roller"
ENHET_SEARCH_URL = BRREG_BASE + "/enhetsregisteret/api/enheter"

HUMAN_ENHET_URL = "https://virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}"
HUMAN_UNDERENHET_URL = "https://virksomhet.brreg.no/nb/oppslag/underenheter/{orgnr}"

SOURCE_ENHETSREGISTERET = "Brønnøysundregistrene: Enhetsregisteret"
SOURCE_ROLLER = "Brønnøysundregistrene: Enhetsregisteret roller"
SOURCE_REGNSKAP = "Brønnøysundregistrene: Regnskapsregisteret"
SOURCE_WEBSITE = "Company website (listed in Enhetsregisteret)"

HTTP_TIMEOUT_SECONDS = 15.0
WEBSITE_TIMEOUT_SECONDS = 12.0
WEBSITE_MAX_BYTES = 1_500_000
HTTP_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.5

# Budgets. Override with --budget / SIGNALPOST_REQUEST_BUDGET and --max-minutes / SIGNALPOST_MAX_MINUTES.
# The published daily limit was 2,000 requests for 100 companies, i.e. 20 per company.
REQUESTS_PER_COMPANY = 18
FIXED_RUN_REQUESTS = 150  # NAV feed pages and batch lookups, independent of batch size
DEFAULT_REQUEST_BUDGET = 1900
DEFAULT_MAX_MINUTES = 30.0
DEFAULT_WORKERS = 16
FILING_HISTORY_INTERVAL_SECONDS = 2.1  # Regnskapsregisteret copy index allows ~30 requests/minute
SUBUNITS_LISTED = 20

# Profiles older than this are refreshed in full by `update --stale-days`.
DEFAULT_STALE_DAYS = 30

# Per-host politeness: minimum seconds between requests, and a cool-down after HTTP 429.
HOST_MIN_INTERVAL_SECONDS = {"pam-stilling-feed.nav.no": 0.25}
HOST_COOLDOWN_SECONDS = {"pam-stilling-feed.nav.no": 600}
JOBS_FEED_DAYS = 45  # NAV feed window for active ads
