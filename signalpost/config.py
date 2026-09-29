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
WEBSITE_TIMEOUT_SECONDS = 8.0
WEBSITE_MAX_BYTES = 1_500_000
HTTP_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.5

# Daily evaluation limits are 2,000 outbound requests and 45 minutes.
DEFAULT_REQUEST_BUDGET = 1900
DEFAULT_WORKERS = 8
SUBUNITS_LISTED = 5

# Profiles older than this are refreshed in full by `update --stale-days`.
DEFAULT_STALE_DAYS = 30
