# Known limitations

- **Change history** covers what Brønnøysund records: entity fields, and dates of role updates. Role-feed events carry no detail, so the roles themselves are compared through the stored snapshot.
- **Websites.** Most companies in the universe are small holding companies and housing co-ops without a website. Discovery only publishes a website the gate can prove, so coverage is deliberately conservative. JavaScript-only sites are not rendered.
- **Jobs.** NAV's official feed excludes Finn.no ads, and most small companies advertise rarely, so job coverage is low. Reading the feed takes about 3 to 5 minutes per run. It runs in parallel with the main pass. NAV's search endpoint was tested and dropped: it returns HTTP 429 after a handful of calls.
- **Filing history** is capped by the upstream limit of about 30 requests per minute. In a very large batch, companies not reached before the time budget are reported as `blocked`.
- **Accounts.** The open accounts API returns the latest filed year only. Earlier years are listed as official PDF copies, which are not parsed.
- **No shareholder or beneficial-owner data.** Those registers are not openly available for automated use.
- **No news or reviews from third-party platforms.** Only dated items on the company's own verified website and official registry events are included.
