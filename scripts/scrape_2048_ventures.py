from __future__ import annotations

import logging
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests import RequestException, Response, Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from portco_scraper.utils import clean_text, normalize_company_name, write_csv, write_json


BASE_URL = "https://www.2048.vc"
SOURCE_URL = "https://www.2048.vc/companies"
OUTPUT_DIR = PROJECT_ROOT / "data" / "2048_ventures"
JSON_OUTPUT_PATH = OUTPUT_DIR / "companies.json"
CSV_OUTPUT_PATH = OUTPUT_DIR / "companies.csv"
REQUEST_TIMEOUT_SECONDS = 30

FIELDNAMES = [
    "company_name",
    "description",
    "founders",
    "founder_titles",
    "fund",
    "location",
    "announcement_text",
    "announcement_urls",
    "company_profile_url",
    "company_url",
    "why_we_invested_url",
    "founder_image_urls",
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
    response = request_url(session, SOURCE_URL)
    scraped_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    soup = BeautifulSoup(response.text, "html.parser")
    records = [
        parse_company_card(card, scraped_at=scraped_at)
        for card in soup.select(".card-investment")
    ]
    records = [record for record in records if record]

    logger.info("Parsed %s raw 2048 Ventures company records from HTML", len(records))
    deduped = dedupe_by_company_name(records)
    logger.info("Scraped %s 2048 Ventures companies after dedupe", len(deduped))
    return deduped


def parse_company_card(card: BeautifulSoup, *, scraped_at: str) -> dict[str, Any] | None:
    company_link = card.select_one("a.link-companies[href]")
    company_name = clean_text(company_link)
    if not company_name:
        logger.warning("Skipping 2048 Ventures card with no company name")
        return None

    fund_location = [clean_text(item) for item in card.select(".button-outline-invested div")]
    fund_location = [item for item in fund_location if item]
    why_link = visible_link(card.select_one("a.button-invested[href]"))
    announcement = card.select_one(".companies-body")

    return {
        "company_name": company_name,
        "description": clean_text(card.select_one(".companies-description p")),
        "founders": visible_texts(card.select(".partner-name")),
        "founder_titles": visible_texts(card.select(".partner-title")),
        "fund": fund_location[0] if fund_location else None,
        "location": fund_location[1] if len(fund_location) > 1 else None,
        "announcement_text": clean_text(announcement),
        "announcement_urls": links_from(announcement),
        "company_profile_url": None,
        "company_url": clean_text(company_link.get("href")) if company_link else None,
        "why_we_invested_url": why_link,
        "founder_image_urls": image_urls(card.select(".partner-avatar[src]")),
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


def visible_texts(elements: list[Any]) -> list[str]:
    values: list[str] = []
    for element in elements:
        classes = element.get("class") or []
        if "w-dyn-bind-empty" in classes:
            continue
        text = clean_text(element)
        if text:
            values.append(text)
    return merge_lists([], values)


def visible_link(element: Any) -> str | None:
    if not element:
        return None
    if "w-condition-invisible" in (element.get("class") or []):
        return None
    href = clean_text(element.get("href"))
    return urljoin(BASE_URL, href) if href else None


def links_from(element: Any) -> list[str]:
    if element is None:
        return []
    urls: list[str] = []
    for link in element.select("a[href]"):
        href = clean_text(link.get("href"))
        if href:
            urls.append(urljoin(BASE_URL, href))
    return merge_lists([], urls)


def image_urls(images: list[Any]) -> list[str]:
    urls: list[str] = []
    for image in images:
        if "w-dyn-bind-empty" in (image.get("class") or []):
            continue
        src = clean_text(image.get("src"))
        if src:
            urls.append(urljoin(BASE_URL, src))
    return merge_lists([], urls)


def dedupe_by_company_name(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for record in records:
        key = normalize_company_name(record.get("company_name", ""))
        if key not in deduped:
            deduped[key] = record
            continue

        logger.warning("Duplicate 2048 Ventures company name after normalization: %s", record["company_name"])
        deduped[key] = merge_records(deduped[key], record)

    return list(deduped.values())


def merge_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for field in FIELDNAMES:
        old_value = merged.get(field)
        new_value = incoming.get(field)

        if field in {"founders", "founder_titles", "announcement_urls", "founder_image_urls"}:
            merged[field] = merge_lists(old_value or [], new_value or [])
        elif is_empty(old_value) and not is_empty(new_value):
            merged[field] = new_value

    return merged


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
        logger.exception("2048 Ventures scrape failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
