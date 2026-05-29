from __future__ import annotations

import logging
import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from requests import RequestException, Response, Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from portco_scraper.utils import clean_text, normalize_company_name, write_csv, write_json


BASE_URL = "https://www.generalcatalyst.com"
SOURCE_URL = "https://www.generalcatalyst.com/portfolio"
ALGOLIA_APP_ID = "ID4635ZLKJ"
ALGOLIA_SEARCH_API_KEY = "871677f0423646c1278b67120f5adcc0"
ALGOLIA_INDEX_NAME = "gc_primary"
ALGOLIA_QUERY_URL = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX_NAME}/query"
OUTPUT_DIR = PROJECT_ROOT / "data" / "general_catalyst"
JSON_OUTPUT_PATH = OUTPUT_DIR / "companies.json"
CSV_OUTPUT_PATH = OUTPUT_DIR / "companies.csv"
REQUEST_TIMEOUT_SECONDS = 30
HITS_PER_PAGE = 1000

FIELDNAMES = [
    "company_name",
    "description",
    "sectors",
    "primary_sector",
    "investors",
    "first_partnered_year",
    "first_partnered_date",
    "status",
    "status_flags",
    "exit_status",
    "company_profile_url",
    "company_url",
    "linkedin_url",
    "twitter_url",
    "image_url",
    "source_url",
    "scraped_at",
]

STATUS_FIELDS = [
    "active",
    "acquired",
    "ipo",
    "alumni",
    "global",
    "create",
    "cvf",
    "seed",
]

USER_AGENT = (
    "portco-scraper/0.1 "
    "(public data mirror; https://github.com/Supermech24/VC-Comps-Portco-Scraper)"
)

logger = logging.getLogger(__name__)


def build_session() -> Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "X-Algolia-API-Key": ALGOLIA_SEARCH_API_KEY,
            "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        }
    )
    return session


def post_json(session: Session, url: str, payload: dict[str, Any]) -> Response:
    try:
        response = session.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response
    except RequestException as exc:
        raise RuntimeError(f"Failed to fetch {url}: {exc}") from exc


def scrape_companies() -> list[dict[str, Any]]:
    session = build_session()
    scraped_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    raw_companies = fetch_portfolio_hits(session)
    logger.info("Parsed %s raw General Catalyst portfolio records from Algolia", len(raw_companies))

    records = [
        normalize_company_record(company, scraped_at=scraped_at)
        for company in raw_companies
        if isinstance(company, dict)
    ]
    records = [record for record in records if record]

    deduped = dedupe_by_company_name(records)
    logger.info("Scraped %s General Catalyst companies after dedupe", len(deduped))
    return deduped


def fetch_portfolio_hits(session: Session) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    page = 0
    nb_pages = 1

    while page < nb_pages:
        response = post_json(
            session,
            ALGOLIA_QUERY_URL,
            {
                "query": "",
                "hitsPerPage": HITS_PER_PAGE,
                "page": page,
                "facetFilters": ["type:portfolio"],
            },
        )
        payload = response.json()
        page_hits = payload.get("hits")
        if not isinstance(page_hits, list):
            raise RuntimeError("General Catalyst Algolia response did not contain a hits list")

        hits.extend(hit for hit in page_hits if isinstance(hit, dict))
        nb_pages = int(payload.get("nbPages") or 0)
        page += 1

    return hits


def normalize_company_record(company: dict[str, Any], *, scraped_at: str) -> dict[str, Any] | None:
    company_name = clean_text(company.get("name") or company.get("title"))
    if not company_name:
        logger.warning("Skipping General Catalyst company with no name: %s", company.get("objectID"))
        return None

    backed_date = clean_text(company.get("gc-backed-since"))
    slug = clean_text(company.get("slug"))

    return {
        "company_name": company_name,
        "description": clean_text(company.get("description")),
        "sectors": normalize_list(company.get("sectors")),
        "primary_sector": clean_text(company.get("primary-sector")),
        "investors": normalize_list(company.get("investors")),
        "first_partnered_year": normalize_year(backed_date),
        "first_partnered_date": normalize_date(backed_date),
        "status": infer_status(company),
        "status_flags": status_flags(company),
        "exit_status": clean_text(company.get("gc-exit")),
        "company_profile_url": urljoin(BASE_URL + "/", slug) if slug else None,
        "company_url": clean_text(company.get("website")),
        "linkedin_url": clean_text(company.get("linkedin-link")),
        "twitter_url": clean_text(company.get("x-link")),
        "image_url": first_media_url(company.get("featured-image")) or first_media_url(company.get("logo")),
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


def normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in (clean_text(part) for part in value) if item]

    text = clean_text(value)
    if not text:
        return []

    return [item for item in (clean_text(part) for part in text.split(",")) if item]


def infer_status(company: dict[str, Any]) -> str | None:
    if company.get("ipo") is True:
        return "ipo"
    if company.get("acquired") is True:
        return "acquired"
    if company.get("alumni") is True:
        return "alumni"
    if company.get("active") is True:
        return "active"
    return None


def status_flags(company: dict[str, Any]) -> list[str]:
    return [field for field in STATUS_FIELDS if company.get(field) is True]


def first_media_url(value: Any) -> str | None:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        for key in ("url", "src", "file", "href"):
            url = clean_text(value.get(key))
            if url:
                return url
    return None


def normalize_date(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if "T" in text:
        return text.split("T", 1)[0]
    return text


def dedupe_by_company_name(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for record in records:
        key = normalize_company_name(record.get("company_name", ""))
        if key not in deduped:
            deduped[key] = record
            continue

        logger.warning("Duplicate General Catalyst company name after normalization: %s", record["company_name"])
        deduped[key] = merge_records(deduped[key], record)

    return list(deduped.values())


def merge_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for field in FIELDNAMES:
        old_value = merged.get(field)
        new_value = incoming.get(field)

        if field in {"sectors", "investors", "status_flags"}:
            merged[field] = merge_lists(old_value or [], new_value or [])
        elif is_empty(old_value) and not is_empty(new_value):
            merged[field] = new_value

    return merged


def normalize_year(value: Any) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    match = re_search_year(text)
    return int(match) if match else None


def re_search_year(value: str) -> str | None:
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return match.group(0) if match else None


def merge_lists(existing: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*existing, *incoming]:
        text = clean_text(value)
        if not text:
            continue
        key = text.casefold()
        if key not in seen:
            merged.append(text)
            seen.add(key)
    return merged


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        companies = scrape_companies()
        write_json(JSON_OUTPUT_PATH, companies)
        write_csv(CSV_OUTPUT_PATH, companies, FIELDNAMES)
        logger.info("Wrote %s", JSON_OUTPUT_PATH)
        logger.info("Wrote %s", CSV_OUTPUT_PATH)
        return 0
    except Exception:
        logger.exception("General Catalyst scrape failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
