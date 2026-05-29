# Portco Scraper

Small public-data scrapers that mirror VC portfolio pages into AI-readable files.

## Current Sources

| Firm | Source | JSON | CSV |
| --- | --- | --- | --- |
| Sequoia Capital | https://www.sequoiacap.com/our-companies/ | `data/sequoia/companies.json` | `data/sequoia/companies.csv` |

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

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/scrape_sequoia.py
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\scrape_sequoia.py
```

The scraper writes:

- `data/sequoia/companies.json`
- `data/sequoia/companies.csv`

## Raw GitHub URLs

After pushing this repository to GitHub, the raw JSON URL will be:

```text
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/sequoia/companies.json
```

The raw CSV URL will be:

```text
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/sequoia/companies.csv
```

Replace `<OWNER>/<REPO>` with your GitHub owner and repository name. If your default branch is not `main`, replace `main` with the default branch name.

## Automation

`.github/workflows/update-sequoia.yml` runs the Sequoia scraper weekly and can also be started manually from the GitHub Actions tab. It commits updated JSON and CSV files back to the repository only when the generated files change.

## Adding More Firms

Keep each firm isolated:

- Put new scrapers in `scripts/scrape_<firm>.py`.
- Put shared helpers in `src/portco_scraper/`.
- Write outputs to `data/<firm>/companies.json` and `data/<firm>/companies.csv`.
- Prefer official structured endpoints before falling back to HTML parsing.

## Progress

- Sequoia Capital: scraper implemented with JSON and CSV output.
- Next: add additional VC firm scrapers under the same `data/<firm>/` layout.
