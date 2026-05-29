from __future__ import annotations

import html
import json
import logging
import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests import RequestException, Response, Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from portco_scraper.utils import clean_text, normalize_company_name, write_csv, write_json


SOURCE_URL = "https://a16z.com/portfolio/"
OUTPUT_DIR = PROJECT_ROOT / "data" / "a16z"
JSON_OUTPUT_PATH = OUTPUT_DIR / "companies.json"
CSV_OUTPUT_PATH = OUTPUT_DIR / "companies.csv"
REQUEST_TIMEOUT_SECONDS = 30

FIELDNAMES = [
    "company_name",
    "description",
    "current_stage",
    "founders",
    "year_founded",
    "initial_investment_year",
    "initial_investment_date",
    "exit_year",
    "exit_date",
    "ticker_symbol",
    "acquirer",
    "company_url",
    "announcement_url",
    "announcement_excerpt",
    "social_urls",
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
    response = request_url(session, SOURCE_URL)
    scraped_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    raw_companies = extract_portfolio_companies(response.text)
    logger.info("Parsed %s raw a16z portfolio records", len(raw_companies))

    records = [
        normalize_company_record(company, scraped_at=scraped_at)
        for company in raw_companies
    ]
    records = [record for record in records if record]

    deduped = dedupe_by_company_name(records)
    logger.info("Scraped %s companies after dedupe", len(deduped))
    return deduped


def extract_portfolio_companies(page_html: str) -> list[dict[str, Any]]:
    match = re.search(
        r"window\.a16z_portfolio_companies\s*=\s*(\[.*?\]);",
        page_html,
        flags=re.DOTALL,
    )
    if not match:
        raise RuntimeError("Could not find window.a16z_portfolio_companies in page HTML")

    payload = html.unescape(match.group(1))
    try:
        companies = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse embedded a16z portfolio JSON: {exc}") from exc

    if not isinstance(companies, list):
        raise RuntimeError("Embedded a16z portfolio JSON was not a list")

    return [company for company in companies if isinstance(company, dict)]


def normalize_company_record(company: dict[str, Any], *, scraped_at: str) -> dict[str, Any] | None:
    company_name = clean_text(company.get("title"))
    if not company_name:
        logger.warning("Skipping a16z company with no title: %s", company.get("id"))
        return None

    announcement = company.get("announcement")
    if not isinstance(announcement, dict):
        announcement = {}

    description = clean_text(company.get("overview")) or clean_text(announcement.get("excerpt"))
    initial_investment_date = normalize_date(company.get("invest_date"))
    exit_date = normalize_date(company.get("exit_date"))

    return {
        "company_name": company_name,
        "description": description,
        "current_stage": normalize_stage(company.get("stages") or company.get("stage")),
        "founders": split_names(company.get("founders")),
        "year_founded": normalize_year(company.get("year_founded")),
        "initial_investment_year": normalize_year(initial_investment_date),
        "initial_investment_date": initial_investment_date,
        "exit_year": normalize_year(exit_date),
        "exit_date": exit_date,
        "ticker_symbol": clean_text(company.get("ticker_symbol")),
        "acquirer": clean_text(company.get("acquirer")),
        "company_url": clean_text(company.get("web")),
        "announcement_url": clean_text(announcement.get("permalink")),
        "announcement_excerpt": clean_text(announcement.get("excerpt")),
        "social_urls": extract_social_urls(company.get("socials")),
        "logo_url": clean_text(company.get("logo")),
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


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

        if field in {"founders", "social_urls"}:
            merged[field] = merge_lists(old_value or [], new_value or [])
        elif is_empty(old_value) and not is_empty(new_value):
            merged[field] = new_value

    return merged


def normalize_stage(value: Any) -> list[str]:
    if isinstance(value, list):
        return [stage for stage in (clean_text(item) for item in value) if stage]
    stage = clean_text(value)
    return [stage] if stage else []


def split_names(value: Any) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [
        name
        for name in (clean_text(part) for part in re.split(r"\s*(?:,|;|\band\b|&)\s*", text))
        if name
    ]


def extract_social_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    urls: list[str] = []
    for item in value:
        if isinstance(item, dict):
            url = clean_text(item.get("url") or item.get("link"))
            if url:
                urls.append(url)
    return merge_lists([], urls)


def normalize_date(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None

    match = re.match(r"^((?:19|20)\d{2})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?", text)
    if not match:
        return text

    year = match.group(1)
    month = match.group(2)
    day = match.group(3)
    if month and day:
        return f"{year}-{int(month):02d}-{int(day):02d}"
    if month:
        return f"{year}-{int(month):02d}"
    return year


def normalize_year(value: Any) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return int(match.group(0)) if match else None


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
        logger.exception("a16z scrape failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
