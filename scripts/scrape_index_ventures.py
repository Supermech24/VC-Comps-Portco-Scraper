from __future__ import annotations

import html
import json
import logging
import re
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests import HTTPError
from requests import RequestException, Response, Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from portco_scraper.utils import clean_text, normalize_company_name, write_csv, write_json


BASE_URL = "https://www.indexventures.com"
SOURCE_URL = "https://www.indexventures.com/companies/backed/all/"
OUTPUT_DIR = PROJECT_ROOT / "data" / "index_ventures"
JSON_OUTPUT_PATH = OUTPUT_DIR / "companies.json"
CSV_OUTPUT_PATH = OUTPUT_DIR / "companies.csv"
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 0.35
RETRY_STATUS_CODES = {403, 429}

FIELDNAMES = [
    "company_name",
    "description",
    "sectors",
    "regions",
    "backed",
    "index_team",
    "founders",
    "ticker_symbol",
    "company_profile_url",
    "company_url",
    "image_url",
    "featured_content_urls",
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
    backoff_seconds = [0, 10, 30]
    last_error: Exception | None = None

    for attempt, delay_seconds in enumerate(backoff_seconds, start=1):
        if delay_seconds:
            logger.info("Retrying %s after %s seconds", url, delay_seconds)
            time.sleep(delay_seconds)

        try:
            response = session.get(timeout=REQUEST_TIMEOUT_SECONDS, url=url, **kwargs)
            response.raise_for_status()
            return response
        except HTTPError as exc:
            last_error = exc
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code not in RETRY_STATUS_CODES or attempt == len(backoff_seconds):
                break
            continue
        except RequestException as exc:
            last_error = exc
            if attempt == len(backoff_seconds):
                break
            continue

    raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error


def scrape_companies() -> list[dict[str, Any]]:
    session = build_session()
    scraped_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    listing_response = request_url(session, SOURCE_URL)
    listing_soup = BeautifulSoup(listing_response.text, "html.parser")
    label_maps = parse_filter_label_maps(listing_soup)
    listing_records = parse_company_listing(listing_soup, label_maps=label_maps)
    logger.info("Parsed %s Index Ventures listing records", len(listing_records))

    records: list[dict[str, Any]] = []
    for index, listing_record in enumerate(listing_records, start=1):
        profile_url = listing_record["company_profile_url"]
        try:
            if index > 1:
                time.sleep(REQUEST_DELAY_SECONDS)
            detail_response = request_url(session, profile_url)
            detail_record = parse_company_detail(
                detail_response.text,
                profile_url=profile_url,
                source_url=SOURCE_URL,
                scraped_at=scraped_at,
            )
            records.append(merge_listing_and_detail(listing_record, detail_record))
        except Exception as exc:
            logger.warning("Falling back to listing-only data for %s: %s", profile_url, exc)
            fallback = {field: None for field in FIELDNAMES}
            fallback.update(listing_record)
            fallback["source_url"] = SOURCE_URL
            fallback["scraped_at"] = scraped_at
            records.append(fallback)

        if index % 50 == 0:
            logger.info("Fetched %s/%s Index Ventures profile pages", index, len(listing_records))

    deduped = dedupe_by_company_name(records)
    logger.info("Scraped %s companies after dedupe", len(deduped))
    return deduped


def parse_filter_label_maps(soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    return {
        "backed": parse_select_options(soup, "backed"),
        "regions": parse_select_options(soup, "region"),
        "sectors": parse_select_options(soup, "sector"),
    }


def parse_select_options(soup: BeautifulSoup, name: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for option in soup.select(f'select[name="{name}"] option'):
        value = clean_text(option.get("value"))
        label = clean_text(option)
        if value and label:
            options[value] = label
    return options


def parse_company_listing(
    soup: BeautifulSoup,
    *,
    label_maps: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in soup.select("li.js-company"):
        link = item.select_one("a.companies__relationships__list__item__link[href]")
        if not link:
            continue

        ticker = clean_text(link.select_one(".ticker-symbol"))
        if link.select_one(".ticker-symbol"):
            link.select_one(".ticker-symbol").extract()

        company_name = clean_text(link)
        href = clean_text(link.get("href"))
        if not company_name or not href:
            continue

        records.append(
            {
                "company_name": company_name,
                "sectors": translate_slugs(parse_json_attr(item.get("data-sectors")), label_maps["sectors"]),
                "regions": translate_slugs(parse_json_attr(item.get("data-regions")), label_maps["regions"]),
                "backed": translate_slugs(parse_json_attr(item.get("data-backed")), label_maps["backed"]),
                "ticker_symbol": ticker,
                "company_profile_url": urljoin(BASE_URL, href),
            }
        )

    return records


def parse_company_detail(
    page_html: str,
    *,
    profile_url: str,
    source_url: str,
    scraped_at: str,
) -> dict[str, Any]:
    soup = BeautifulSoup(page_html, "html.parser")
    company_name = clean_text(soup.select_one(".page-hero__headline")) or meta_content(soup, "og:title")
    if company_name and company_name.endswith(" | Index Ventures"):
        company_name = company_name.removesuffix(" | Index Ventures").strip()

    description = clean_text(soup.select_one(".page-hero__subhead")) or meta_content(soup, "og:description")
    if company_name and description and description.startswith(f"{company_name}:"):
        description = clean_text(description[len(company_name) + 1 :])

    detail_sections = parse_detail_sections(soup)
    company_url = first_external_website_url(soup) or first_value(detail_sections.get("Website", []))

    return {
        "company_name": company_name,
        "description": description,
        "sectors": detail_sections.get("Sector", []),
        "regions": [],
        "backed": [],
        "index_team": detail_sections.get("Index Team", []),
        "founders": detail_sections.get("Founders/CEOs", []),
        "ticker_symbol": clean_text(soup.select_one(".page-hero__ticker-symbol")),
        "company_profile_url": profile_url,
        "company_url": company_url,
        "image_url": meta_content(soup, "og:image") or first_image_url(soup),
        "featured_content_urls": featured_content_urls(soup),
        "source_url": source_url,
        "scraped_at": scraped_at,
    }


def parse_detail_sections(soup: BeautifulSoup) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    for column in soup.select(".company-description__col"):
        header = clean_text(column.select_one(".company-description__header"))
        if not header:
            continue

        if header == "Founders/CEOs":
            values = [clean_text(item) for item in column.select(".founder-list__item")]
        else:
            values = [clean_text(item) for item in column.select(".company-description__item")]

        sections[header] = [value for value in values if value]

    return sections


def merge_listing_and_detail(
    listing_record: dict[str, Any],
    detail_record: dict[str, Any],
) -> dict[str, Any]:
    merged = {field: None for field in FIELDNAMES}
    merged.update(detail_record)

    for field in ("sectors", "regions", "backed"):
        merged[field] = merge_lists(listing_record.get(field, []), detail_record.get(field, []))

    for field in ("company_name", "ticker_symbol", "company_profile_url"):
        if is_empty(merged.get(field)) and not is_empty(listing_record.get(field)):
            merged[field] = listing_record[field]

    return merged


def dedupe_by_company_name(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for record in records:
        key = normalize_company_name(record.get("company_name", ""))
        if key not in deduped:
            deduped[key] = record
            continue

        logger.warning("Duplicate company name after normalization: %s", record["company_name"])
        deduped[key] = merge_records(deduped[key], record)

    return list(deduped.values())


def merge_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for field in FIELDNAMES:
        old_value = merged.get(field)
        new_value = incoming.get(field)

        if field in {"sectors", "regions", "backed", "index_team", "founders", "featured_content_urls"}:
            merged[field] = merge_lists(old_value or [], new_value or [])
        elif is_empty(old_value) and not is_empty(new_value):
            merged[field] = new_value

    return merged


def parse_json_attr(value: Any) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(html.unescape(str(value)))
    except json.JSONDecodeError:
        logger.warning("Could not parse JSON data attribute: %s", value)
        return []

    if not isinstance(parsed, list):
        return []
    return [text for text in (clean_text(item) for item in parsed) if text]


def translate_slugs(values: list[str], labels: dict[str, str]) -> list[str]:
    return [labels.get(value, value) for value in values]


def meta_content(soup: BeautifulSoup, property_name: str) -> str | None:
    tag = soup.select_one(f'meta[property="{property_name}"]') or soup.select_one(f'meta[name="{property_name}"]')
    return clean_text(tag.get("content")) if tag else None


def first_external_website_url(soup: BeautifulSoup) -> str | None:
    link = soup.select_one(".company-description__link--external[href]")
    return clean_text(link.get("href")) if link else None


def first_image_url(soup: BeautifulSoup) -> str | None:
    image = soup.select_one('article img[src], #main-content img[src]')
    return urljoin(BASE_URL, image.get("src")) if image else None


def featured_content_urls(soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for link in soup.select(".featured-posts a[href]"):
        href = clean_text(link.get("href"))
        if href:
            urls.append(urljoin(BASE_URL, href))
    return merge_lists([], urls)


def first_value(values: list[str]) -> str | None:
    return values[0] if values else None


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
        logger.exception("Index Ventures scrape failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
