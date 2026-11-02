# How often each source type is right

Hand-checked sample from three v3 test runs on 1 November 2026 (100 random companies; 19 companies that had news or job entries in v2; 300 random companies that list a website or have at least 5 employees), at most one entry per company per source type. Each entry was opened at its source link and marked right, wrong or unsure. Precision is right / (right + wrong).

| Source type | What counts as right | Checked | Right | Wrong | Unsure | Precision |
|---|---|---:|---:|---:|---:|---:|
| official website | the site belongs to this company (not a parent group, a sibling or a directory) | 50 | 50 | 0 | 0 | 100% |
| social profile | the account belongs to this company, not a parent group, franchise chain or web agency | 45 | 42 | 3 | 0 | 93% |
| website news | a dated article (not a home or service page) that is about this company | 7 | 4 | 3 | 0 | 57% |
| nav job | an open job ad on NAV whose employer is this company or one of its workplaces | 5 | 5 | 0 | 0 | 100% |

## By confidence level

| Source type | Confidence | Checked | Precision |
|---|---|---:|---:|
| official website | high | 35 | 100% |
| official website | medium | 15 | 100% |
| social profile | high | 36 | 94% |
| social profile | medium | 9 | 89% |
| website news | high | 2 | 0% |
| website news | medium | 5 | 80% |
| nav job | high | 5 | 100% |

## Wrong entries and what we changed

- social_profile: NTT DATA BUSINESS SOLUTIONS AS (992036079): linkedin: https://www.linkedin.com/company/ntt-data-business-solutions. Global NTT DATA Business Solutions page, not the Norwegian AS. Fixed: unmarked accounts on a site that links the group's regional accounts are left out
- social_profile: JYSK AS (947477129): tiktok: https://www.tiktok.com/@jysk. Global JYSK TikTok account (parent group), not JYSK AS Norway. Fixed: when a site links Norway-marked accounts (jysknorge, JYSK.no), its unmarked accounts are left out as the group's.
- social_profile: SLAKTERIET BREKKE AS (979598009): instagram: https://www.instagram.com/slakterietas. Profile name 'Slakteriet AS'; bio 'Slakteriet AS er et moderne slakteri for oppdrettsfisk med anlegg i Florø og Brekke'; links to slakteriet-as.no. It is the account of sibling SLAKTERIET AS (996679489), which runs the Brekke plant, not Slakteriet Brekke AS. Fixed: social accounts are no longer published from a site that states other companies' org numbers.
- website_news: DEN NORSKE ISRAELSMISJON (970167978): – Det som var en prioritet for Paulus, bør være det for oss kristne også › Den Norske Israelsmisjon. Dated article on the company's site, but it does not name the company; the match came from the site name in the page title. Fixed: the site-name suffix is removed from titles before matching, so this is now 'can't tell'
- website_news: TECHANO OCEANLIFT AS (918401172): Information - Techano Oceanlift. techano.no/information/ is a static information page that WordPress marks og:type=article, not an article. Fixed: pages the CMS marks as pages (body class page-id, AboutPage markup) are never news
- website_news: AKUTT KIROPRAKTIKK OSLO AS (928504026): Om Akutt Kiropraktikk Oslo - Akutt Kiropraktikk Oslo. /om-akutt-kiropraktikk-oslo/ is the About page, not an article. Fixed: pages the CMS marks as pages (body class page-id, AboutPage markup) are never news
