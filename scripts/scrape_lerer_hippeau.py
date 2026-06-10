from __future__ import annotations

import logging
import re
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup
from requests import RequestException, Response, Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from portco_scraper.utils import clean_text, normalize_company_name, write_csv, write_json


BASE_URL = "https://www.lererhippeau.com"
SOURCE_URL = "https://www.lererhippeau.com/portfolio"
OUTPUT_DIR = PROJECT_ROOT / "data" / "lerer_hippeau"
JSON_OUTPUT_PATH = OUTPUT_DIR / "companies.json"
CSV_OUTPUT_PATH = OUTPUT_DIR / "companies.csv"
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 0.25
MAX_PAGES = 50

FIELDNAMES = [
    "company_name",
    "description",
    "status",
    "first_partnered_year",
    "company_url",
    "logo_url",
    "source_url",
    "scraped_at",
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
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        }
    )
    return session


def request_url(session: Session, url: str, **kwargs: Any) -> Response:
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
        response.raise_for_status()
        return response
    except RequestException as exc:
        raise RuntimeError(f"Failed to fetch {url}: {exc}") from exc


def scrape_companies() -> list[dict[str, Any]]:
    session = build_session()
    scraped_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    records: list[dict[str, Any]] = []
    url: str | None = SOURCE_URL
    visited: set[str] = set()
    page = 0

    while url and url not in visited and page < MAX_PAGES:
        visited.add(url)
        page += 1
        response = request_url(session, url)
        soup = BeautifulSoup(response.text, "html.parser")

        page_records = parse_page_records(soup, scraped_at=scraped_at)
        records.extend(page_records)
        logger.info("Parsed %s raw Lerer Hippeau company records from page %s", len(page_records), page)

        url = next_page_url(soup)
        if url:
            time.sleep(REQUEST_DELAY_SECONDS)

    deduped = dedupe_by_company_name(records)
    logger.info("Scraped %s Lerer Hippeau companies after dedupe", len(deduped))
    return deduped


def parse_page_records(soup: BeautifulSoup, *, scraped_at: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in soup.select(".portfolio-collection-list > .w-dyn-item"):
        record = parse_company_item(item, scraped_at=scraped_at)
        if record:
            records.append(record)
    return records


def parse_company_item(item: Any, *, scraped_at: str) -> dict[str, Any] | None:
    company_name = company_name_from_item(item)
    if not company_name:
        logger.warning("Skipping Lerer Hippeau item with no company name")
        return None

    return {
        "company_name": company_name,
        "description": clean_text(item.select_one("p.portfolio-details")),
        "status": company_status(item),
        "first_partnered_year": partnered_year(item),
        "company_url": company_url(item),
        "logo_url": logo_url(item),
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


def company_name_from_item(item: Any) -> str | None:
    parts = [
        text
        for text in (clean_text(node) for node in item.select("a.link-holder .website-url"))
        if text and text not in {"Visit", "→"}
    ]
    name = clean_text(" ".join(parts))
    if name:
        return name

    return name_from_logo(item)


def name_from_logo(item: Any) -> str | None:
    logo = item.select_one("img.potfolio-logo, img.hover-logo")
    src = clean_text(logo.get("src")) if logo else None
    if not src:
        return None

    filename = src.rsplit("/", 1)[-1]
    match = re.search(r"_([^_]+)\.\w+$", filename)
    if not match:
        return None

    return clean_text(unquote(match.group(1)))


def company_status(item: Any) -> str:
    return "Exited" if item.select_one(".exit-tag") else "Active"


def partnered_year(item: Any) -> int | None:
    for node in item.select(".year-holder .size"):
        text = clean_text(node)
        if text and text.isdigit():
            return int(text)
    return None


def company_url(item: Any) -> str | None:
    link = item.select_one("a.link-holder[href]")
    href = clean_text(link.get("href")) if link else None
    if not href or href == "#":
        return None
    return urljoin(BASE_URL, href)


def logo_url(item: Any) -> str | None:
    logo = item.select_one("img.potfolio-logo[src], img.hover-logo[src]")
    src = clean_text(logo.get("src")) if logo else None
    return urljoin(BASE_URL, src) if src else None


def next_page_url(soup: BeautifulSoup) -> str | None:
    link = soup.select_one(".w-pagination-next[href]")
    href = clean_text(link.get("href")) if link else None
    return urljoin(SOURCE_URL, href) if href else None


def dedupe_by_company_name(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for record in records:
        key = normalize_company_name(record.get("company_name", ""))
        if key not in deduped:
            deduped[key] = record
            continue

        deduped[key] = merge_records(deduped[key], record)

    return list(deduped.values())


def merge_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for field in FIELDNAMES:
        old_value = merged.get(field)
        new_value = incoming.get(field)
        if is_empty(old_value) and not is_empty(new_value):
            merged[field] = new_value

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
        logger.exception("Lerer Hippeau scrape failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
