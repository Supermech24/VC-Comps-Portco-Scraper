from __future__ import annotations

import logging
import sys
import time
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


BASE_URL = "https://www.afore.vc"
SOURCE_URL = "https://www.afore.vc/portfolio"
OUTPUT_DIR = PROJECT_ROOT / "data" / "afore"
JSON_OUTPUT_PATH = OUTPUT_DIR / "companies.json"
CSV_OUTPUT_PATH = OUTPUT_DIR / "companies.csv"
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 0.25

FIELDNAMES = [
    "company_name",
    "description",
    "sectors",
    "location",
    "afore_stage",
    "current_stage",
    "follow_on_investors",
    "company_profile_url",
    "company_url",
    "logo_url",
    "image_url",
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

    first_page = request_url(session, SOURCE_URL)
    first_soup = BeautifulSoup(first_page.text, "html.parser")
    records = [
        parse_hidden_company_item(item, scraped_at=scraped_at)
        for item in first_soup.select(".portfolio__list > .w-dyn-item")
    ]
    records = [record for record in records if record]
    logger.info("Parsed %s raw Afore company records from hidden full list", len(records))

    featured_records = parse_all_featured_records(session, first_soup)
    logger.info("Parsed %s Afore featured/enrichment records", len(featured_records))

    merged_records = merge_featured_data(records, featured_records)
    deduped = dedupe_by_company_name(merged_records)
    logger.info("Scraped %s Afore companies after dedupe", len(deduped))
    return deduped


def parse_hidden_company_item(item: Any, *, scraped_at: str) -> dict[str, Any] | None:
    link = item.select_one("a.link-block-2[href]")
    company_name = clean_text(item.select_one("h3.t-48:not(.aquire_tag)"))
    if not company_name:
        logger.warning("Skipping Afore item with no company name")
        return None

    sector = clean_text(item.select_one('[fs-cmsfilter-field="tag"]'))

    return {
        "company_name": company_name,
        "description": clean_text(item.select_one("p.t-16")),
        "sectors": [sector] if sector else [],
        "location": clean_text(item.select_one(".text-all-caps")),
        "afore_stage": None,
        "current_stage": first_visible_stage(item),
        "follow_on_investors": [],
        "company_profile_url": None,
        "company_url": clean_text(link.get("href")) if link else None,
        "logo_url": None,
        "image_url": None,
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


def parse_all_featured_records(session: Session, first_soup: BeautifulSoup) -> list[dict[str, Any]]:
    records = parse_featured_records(first_soup)
    next_url = next_page_url(first_soup)
    seen_pages = {SOURCE_URL}

    while next_url and next_url not in seen_pages:
        seen_pages.add(next_url)
        time.sleep(REQUEST_DELAY_SECONDS)
        response = request_url(session, next_url)
        soup = BeautifulSoup(response.text, "html.parser")
        records.extend(parse_featured_records(soup))
        next_url = next_page_url(soup)

    return records


def parse_featured_records(soup: BeautifulSoup) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in soup.select(".portfolio__featured-list-item"):
        record = parse_featured_item(item)
        if record:
            records.append(record)
    return records


def parse_featured_item(item: Any) -> dict[str, Any] | None:
    company_name = clean_text(item.select_one("h3.t-48:not(.aquire_tag)"))
    company_url = featured_company_url(item)
    success_story_url = internal_link(item.select_one("a.cms-nest-link-2[href]"))

    if not company_name and not company_url:
        return None

    sector = clean_text(item.select_one('[fs-cmsfilter-field="tag"], .portfolio__tag .f-roboto'))
    locations = [
        value
        for value in (clean_text(node) for node in item.select('[fs-cmsfilter-field="Locations"]'))
        if value and value != "ALL" and not value.endswith("-false")
    ]

    return {
        "company_name": company_name,
        "description": clean_text(item.select_one("p.t-16, p.is-portfolio-2")),
        "sectors": [sector] if sector and sector != "ALL" else [],
        "location": first_value(locations) or clean_text(item.select_one(".text-all-caps.is-portfolio, .tag-item")),
        "afore_stage": clean_text(item.select_one(".tag-pre-seed-wrapper")),
        "current_stage": first_visible_stage(item),
        "follow_on_investors": follow_on_investors(item),
        "company_profile_url": success_story_url,
        "company_url": company_url,
        "logo_url": image_url(item.select_one(".portfolio__company-logo[src]")),
        "image_url": image_url(item.select_one(".content-cover.is-portfolio[src]")),
    }


def merge_featured_data(
    records: list[dict[str, Any]],
    featured_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    by_url: dict[str, dict[str, Any]] = {}

    for record in featured_records:
        name = record.get("company_name")
        if name:
            by_name[normalize_company_name(name)] = record
        url = normalize_url(record.get("company_url"))
        if url:
            by_url[url] = record

    merged: list[dict[str, Any]] = []
    for record in records:
        enriched = by_name.get(normalize_company_name(record["company_name"]))
        if not enriched:
            enriched = by_url.get(normalize_url(record.get("company_url")))
        merged.append(merge_records(record, enriched or {}))

    return merged


def next_page_url(soup: BeautifulSoup) -> str | None:
    link = soup.select_one(".w-pagination-next[href]")
    href = clean_text(link.get("href")) if link else None
    return urljoin(SOURCE_URL, href) if href else None


def featured_company_url(item: Any) -> str | None:
    link = item.select_one("a.portfolio__featured-list-item-link[href], a.portfolio-link[href]")
    href = clean_text(link.get("href")) if link else None
    return urljoin(BASE_URL, href) if href else None


def internal_link(link: Any) -> str | None:
    href = clean_text(link.get("href")) if link else None
    if not href or href == "#":
        return None
    return urljoin(BASE_URL, href)


def first_visible_stage(item: Any) -> str | None:
    for stage in item.select(".portfolio__company-stage"):
        if "w-condition-invisible" not in (stage.get("class") or []):
            text = clean_text(stage)
            if text:
                return text
    return None


def follow_on_investors(item: Any) -> list[str]:
    investors: list[str] = []
    for image in item.select(".follow-on-logo"):
        investor = clean_text(image.get("alt"))
        if investor:
            investors.append(investor)
    return merge_lists([], investors)


def image_url(image: Any) -> str | None:
    src = clean_text(image.get("src")) if image else None
    return urljoin(BASE_URL, src) if src else None


def first_value(values: list[str]) -> str | None:
    return values[0] if values else None


def normalize_url(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    return text.rstrip("/").casefold()


def dedupe_by_company_name(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for record in records:
        key = normalize_company_name(record.get("company_name", ""))
        if key not in deduped:
            deduped[key] = record
            continue

        logger.warning("Duplicate Afore company name after normalization: %s", record["company_name"])
        deduped[key] = merge_records(deduped[key], record)

    return list(deduped.values())


def merge_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for field in FIELDNAMES:
        old_value = merged.get(field)
        new_value = incoming.get(field)

        if field in {"sectors", "follow_on_investors"}:
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
        logger.exception("Afore scrape failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
