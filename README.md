# Portco Scraper

Small public-data scrapers that mirror VC portfolio pages into AI-readable files.

## Current Sources

| Firm | Source | JSON | CSV |
| --- | --- | --- | --- |
| Sequoia Capital | https://www.sequoiacap.com/our-companies/ | `data/sequoia/companies.json` | `data/sequoia/companies.csv` |
| Andreessen Horowitz (a16z) | https://a16z.com/portfolio/ | `data/a16z/companies.json` | `data/a16z/companies.csv` |
| Index Ventures | https://www.indexventures.com/companies/backed/all/ | `data/index_ventures/companies.json` | `data/index_ventures/companies.csv` |
| Accel | https://www.accel.com/companies | `data/accel/companies.json` | `data/accel/companies.csv` |
| General Catalyst | https://www.generalcatalyst.com/portfolio | `data/general_catalyst/companies.json` | `data/general_catalyst/companies.csv` |
| 2048 Ventures | https://www.2048.vc/companies | `data/2048_ventures/companies.json` | `data/2048_ventures/companies.csv` |
| Afore | https://www.afore.vc/portfolio | `data/afore/companies.json` | `data/afore/companies.csv` |
| Lerer Hippeau | https://www.lererhippeau.com/portfolio | `data/lerer_hippeau/companies.json` | `data/lerer_hippeau/companies.csv` |

## Sequoia Scraper

The Sequoia scraper uses only public data from Sequoia's official site.

It first checks the page for structured sources. Sequoia exposes company posts through the public WordPress REST endpoint at `https://sequoiacap.com/wp-json/wp/v2/company`, which provides canonical company profile URLs. The stage, partner, and short description fields are rendered in the public FacetWP listing at `https://www.sequoiacap.com/our-companies/` and its `_paged=N` pages, so the scraper combines those official public sources.

Output fields:

- `company_name`
- `description`
- `current_stage`
- `partners`
- `first_partnered_stage`
- `first_partnered_year`
- `company_profile_url`
- `source_url`
- `scraped_at`

Companies are deduplicated by normalized company name.

## a16z Scraper

The a16z scraper uses only public data from a16z's official portfolio page.

The page embeds structured portfolio data in `window.a16z_portfolio_companies`. The scraper parses that JSON directly instead of scraping visual cards or requiring a browser.

Output fields:

- `company_name`
- `description`
- `current_stage`
- `founders`
- `year_founded`
- `initial_investment_year`
- `initial_investment_date`
- `exit_year`
- `exit_date`
- `ticker_symbol`
- `acquirer`
- `company_url`
- `announcement_url`
- `announcement_excerpt`
- `social_urls`
- `logo_url`
- `source_url`
- `scraped_at`

Companies are deduplicated by normalized company name.

## 2048 Ventures Scraper

The 2048 Ventures scraper uses only public data from 2048 Ventures' official companies page.

The page renders its Webflow CMS company collection directly in the public HTML. No cleaner embedded JSON or backend API was exposed in the page source, so the scraper parses the rendered CMS cards.

Output fields:

- `company_name`
- `description`
- `founders`
- `founder_titles`
- `fund`
- `location`
- `announcement_text`
- `announcement_urls`
- `company_profile_url`
- `company_url`
- `why_we_invested_url`
- `founder_image_urls`
- `source_url`
- `scraped_at`

Companies are deduplicated by normalized company name.

## Afore Scraper

The Afore scraper uses only public data from Afore's official portfolio page.

The page renders a Webflow CMS portfolio collection in the public HTML and uses Finsweet pagination for visible cards. No cleaner embedded JSON or backend API was exposed in the page source. The scraper reads the full hidden list for complete company coverage, then follows the public Webflow pagination to enrich records with visible-card details where available.

Output fields:

- `company_name`
- `description`
- `sectors`
- `location`
- `afore_stage`
- `current_stage`
- `follow_on_investors`
- `company_profile_url`
- `company_url`
- `logo_url`
- `image_url`
- `source_url`
- `scraped_at`

Companies are deduplicated by normalized company name.

## Lerer Hippeau Scraper

The Lerer Hippeau scraper uses only public data from Lerer Hippeau's official portfolio page.

The page is a Webflow CMS site that renders its portfolio collection directly in the public HTML and paginates with Webflow's native pagination (`?c33e6893_page=N`). No cleaner embedded JSON or backend API was exposed in the page source, so the scraper parses the rendered CMS cards and follows the pagination links until every page is read. A featured collection is repeated on each page and overlaps the main alphabetical list, so records are deduplicated by normalized company name.

Output fields:

- `company_name`
- `description`
- `status`
- `first_partnered_year`
- `company_url`
- `logo_url`
- `source_url`
- `scraped_at`

`status` is `Exited` when the card carries an exit tag and `Active` otherwise. Companies are deduplicated by normalized company name.

## Accel Scraper

The Accel scraper uses only public data from Accel's official companies page.

The page is backed by a public Sanity dataset. The scraper queries the same official structured backend for company records instead of scraping rendered cards.

Output fields:

- `company_name`
- `description`
- `current_stage`
- `partners`
- `founders`
- `sectors`
- `first_partnered_stage`
- `first_partnered_year`
- `first_partnered_date`
- `initial_investment_type`
- `exit_type`
- `exit_description`
- `company_profile_url`
- `company_url`
- `twitter_url`
- `logo_url`
- `image_url`
- `source_url`
- `scraped_at`

Companies are deduplicated by normalized company name.

## General Catalyst Scraper

The General Catalyst scraper uses only public data from General Catalyst's official portfolio page.

The page exposes a public Algolia index used by the site's own search experience. The scraper queries the official public index for `Portfolio` records instead of relying on the first 100 rendered Webflow cards.

Output fields:

- `company_name`
- `description`
- `sectors`
- `primary_sector`
- `investors`
- `first_partnered_year`
- `first_partnered_date`
- `status`
- `status_flags`
- `exit_status`
- `company_profile_url`
- `company_url`
- `linkedin_url`
- `twitter_url`
- `image_url`
- `source_url`
- `scraped_at`

Companies are deduplicated by normalized company name.

## Index Ventures Scraper

The Index Ventures scraper uses only public data from Index's official companies pages.

The full company index is rendered in the HTML at `https://www.indexventures.com/companies/backed/all/`. The scraper reads that list for company profile URLs and filter metadata, then fetches each public company profile page for description, founders, Index team, sector, website, ticker, image, and related content links.

Output fields:

- `company_name`
- `description`
- `sectors`
- `regions`
- `backed`
- `index_team`
- `founders`
- `ticker_symbol`
- `company_profile_url`
- `company_url`
- `image_url`
- `featured_content_urls`
- `source_url`
- `scraped_at`

Companies are deduplicated by normalized company name.

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/scrape_sequoia.py
python scripts/scrape_a16z.py
python scripts/scrape_index_ventures.py
python scripts/scrape_accel.py
python scripts/scrape_general_catalyst.py
python scripts/scrape_2048_ventures.py
python scripts/scrape_afore.py
python scripts/scrape_lerer_hippeau.py
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\scrape_sequoia.py
python scripts\scrape_a16z.py
python scripts\scrape_index_ventures.py
python scripts\scrape_accel.py
python scripts\scrape_general_catalyst.py
python scripts\scrape_2048_ventures.py
python scripts\scrape_afore.py
python scripts\scrape_lerer_hippeau.py
```

The scrapers write:

- `data/sequoia/companies.json`
- `data/sequoia/companies.csv`
- `data/a16z/companies.json`
- `data/a16z/companies.csv`
- `data/index_ventures/companies.json`
- `data/index_ventures/companies.csv`
- `data/accel/companies.json`
- `data/accel/companies.csv`
- `data/general_catalyst/companies.json`
- `data/general_catalyst/companies.csv`
- `data/2048_ventures/companies.json`
- `data/2048_ventures/companies.csv`
- `data/afore/companies.json`
- `data/afore/companies.csv`
- `data/lerer_hippeau/companies.json`
- `data/lerer_hippeau/companies.csv`

## Raw GitHub URLs

After pushing this repository to GitHub, the raw JSON URLs will be:

```text
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/sequoia/companies.json
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/a16z/companies.json
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/index_ventures/companies.json
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/accel/companies.json
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/general_catalyst/companies.json
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/2048_ventures/companies.json
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/afore/companies.json
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/lerer_hippeau/companies.json
```

The raw CSV URLs will be:

```text
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/sequoia/companies.csv
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/a16z/companies.csv
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/index_ventures/companies.csv
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/accel/companies.csv
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/general_catalyst/companies.csv
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/2048_ventures/companies.csv
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/afore/companies.csv
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/lerer_hippeau/companies.csv
```

Replace `<OWNER>/<REPO>` with your GitHub owner and repository name. If your default branch is not `main`, replace `main` with the default branch name.

## Automation

`.github/workflows/update-portfolios.yml` runs the Sequoia, a16z, Index Ventures, Accel, General Catalyst, 2048 Ventures, Afore, and Lerer Hippeau scrapers weekly and can also be started manually from the GitHub Actions tab. It commits updated JSON and CSV files back to the repository only when the generated files change.

## Adding More Firms

Keep each firm isolated:

- Put new scrapers in `scripts/scrape_<firm>.py`.
- Put shared helpers in `src/portco_scraper/`.
- Write outputs to `data/<firm>/companies.json` and `data/<firm>/companies.csv`.
- Prefer official structured endpoints before falling back to HTML parsing.

## Progress

- Sequoia Capital: scraper implemented with JSON and CSV output.
- a16z: scraper implemented with JSON and CSV output.
- Index Ventures: scraper implemented with JSON and CSV output.
- Accel: scraper implemented with JSON and CSV output.
- General Catalyst: scraper implemented with JSON and CSV output.
- 2048 Ventures: scraper implemented with JSON and CSV output.
- Afore: scraper implemented with JSON and CSV output.
- Lerer Hippeau: scraper implemented with JSON and CSV output.
- Next: add additional VC firm scrapers under the same `data/<firm>/` layout.
