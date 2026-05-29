from __future__ import annotations

import json
import logging
import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests import RequestException, Response, Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from portco_scraper.utils import clean_text, normalize_company_name, write_csv, write_json


SOURCE_URL = "https://sequoiacap.com/our-companies/"
WP_COMPANY_API_URL = "https://sequoiacap.com/wp-json/wp/v2/company"
OUTPUT_DIR = PROJECT_ROOT / "data" / "sequoia"
JSON_OUTPUT_PATH = OUTPUT_DIR / "companies.json"
CSV_OUTPUT_PATH = OUTPUT_DIR / "companies.csv"
REQUEST_TIMEOUT_SECONDS = 30
MAX_LISTING_PAGES = 100

FIELDNAMES = [
    "company_name",
    "description",
    "current_stage",
    "partners",
    "first_partnered_stage",
    "first_partnered_year",
    "company_profile_url",
    "source_url",
    "scraped_at",
]

USER_AGENT = (
    "portco-scraper/0.1 "
    "(public data mirror; https://github.com/OWNER/REPO)"
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


def fetch_company_profile_links(session: Session) -> dict[str, str]:
    """Fetch canonical company profile links from Sequoia's public WP REST API."""
    profile_links: dict[str, str] = {}
    page = 1
    per_page = 100
    total_pages: int | None = None

    while total_pages is None or page <= total_pages:
        response = request_url(
            session,
            WP_COMPANY_API_URL,
            params={
                "per_page": per_page,
                "page": page,
                "_fields": "id,link,title",
            },
        )
        if total_pages is None:
            total_pages = _parse_positive_int(response.headers.get("X-WP-TotalPages")) or 1
            logger.info("WordPress REST reports %s company pages", total_pages)

        try:
            companies = response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from {response.url}: {exc}") from exc

        if not isinstance(companies, list):
            raise RuntimeError(f"Unexpected WordPress REST response shape from {response.url}")

        if not companies:
            break

        for company in companies:
            company_id = company.get("id")
            link = company.get("link")
            if company_id and link:
                profile_links[str(company_id)] = link

        if len(companies) < per_page and total_pages is None:
            break
        page += 1

    logger.info("Fetched %s company profile links from WordPress REST", len(profile_links))
    return profile_links


def scrape_companies() -> list[dict[str, Any]]:
    session = build_session()
    scraped_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    profile_links = fetch_company_profile_links(session)

    first_page = request_url(session, SOURCE_URL)
    first_page_html = first_page.text
    listing_pages = detect_listing_pages(first_page_html)
    logger.info("FacetWP listing reports %s pages", listing_pages or "an unknown number of")

    records = parse_listing_page(
        first_page_html,
        source_url=SOURCE_URL,
        profile_links=profile_links,
        scraped_at=scraped_at,
    )

    if listing_pages:
        page_numbers = range(2, listing_pages + 1)
    else:
        page_numbers = range(2, MAX_LISTING_PAGES + 1)

    for page_number in page_numbers:
        page_url = listing_page_url(page_number)
        response = request_url(session, page_url)
        page_records = parse_listing_page(
            response.text,
            source_url=page_url,
            profile_links=profile_links,
            scraped_at=scraped_at,
        )

        if not page_records and not listing_pages:
            logger.info("Stopping pagination at page %s after no rows were found", page_number)
            break

        records.extend(page_records)

    deduped = dedupe_by_company_name(records)
    missing_profiles = sum(1 for record in deduped if not record.get("company_profile_url"))
    if missing_profiles:
        logger.warning("%s companies did not have a WordPress profile URL match", missing_profiles)

    logger.info("Scraped %s rows; %s companies after dedupe", len(records), len(deduped))
    return deduped


def detect_listing_pages(html: str) -> int | None:
    match = re.search(r"window\.FWP_JSON\s*=\s*(\{.*?\});", html, flags=re.DOTALL)
    if not match:
        return None

    try:
        facetwp_json = json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("Could not parse embedded FacetWP JSON")
        return None

    pager = (
        facetwp_json.get("preload_data", {})
        .get("settings", {})
        .get("pager", {})
    )
    return _parse_positive_int(pager.get("total_pages"))


def listing_page_url(page_number: int) -> str:
    if page_number <= 1:
        return SOURCE_URL
    return f"{SOURCE_URL}?_paged={page_number}"


def parse_listing_page(
    html: str,
    *,
    source_url: str,
    profile_links: dict[str, str],
    scraped_at: str,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tbody.facetwp-template tr[aria-expanded]")
    if not rows:
        rows = soup.select("table#company_listing tr[aria-expanded]")

    records: list[dict[str, Any]] = []
    for row in rows:
        record = parse_company_row(
            row,
            source_url=source_url,
            profile_links=profile_links,
            scraped_at=scraped_at,
        )
        if record:
            records.append(record)

    logger.info("Parsed %s companies from %s", len(records), source_url)
    return records


def parse_company_row(
    row: Any,
    *,
    source_url: str,
    profile_links: dict[str, str],
    scraped_at: str,
) -> dict[str, Any] | None:
    cells = row.find_all(["td", "th"], recursive=False)
    post_id = clean_text(cells[0]) if cells else None

    company_name = clean_text(row.select_one(".company-listing__head"))
    if not company_name:
        logger.warning("Skipping row with no company name on %s", source_url)
        return None

    first_partnered_cell = _first_cell_with_class(row, "u-xl-hide")
    first_partnered_stage, first_partnered_year = parse_first_partnered(
        clean_text(first_partnered_cell),
        first_partnered_cell.get("data-order") if first_partnered_cell else None,
    )

    return {
        "company_name": company_name,
        "description": clean_text(row.select_one(".company-listing__text")),
        "current_stage": clean_text(_current_stage_cell(row)),
        "partners": _parse_partners(row),
        "first_partnered_stage": first_partnered_stage,
        "first_partnered_year": first_partnered_year,
        "company_profile_url": profile_links.get(post_id or ""),
        "source_url": source_url,
        "scraped_at": scraped_at,
    }


def parse_first_partnered(value: str | None, data_order: str | None = None) -> tuple[str | None, int | None]:
    if not value:
        return None, _parse_year(data_order)

    match = re.match(r"^(?P<stage>.*?)\s*\((?P<year>\d{4})\)\s*$", value)
    if match:
        return clean_text(match.group("stage")), int(match.group("year"))

    year = _parse_year(data_order)
    if year is None:
        year_match = re.search(r"\b(19|20)\d{2}\b", value)
        if year_match:
            year = int(year_match.group(0))

    stage = re.sub(r"\(?\b(19|20)\d{2}\b\)?", "", value).strip()
    return clean_text(stage), year


def dedupe_by_company_name(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for record in records:
        company_name = record.get("company_name")
        if not company_name:
            continue

        key = normalize_company_name(company_name)
        if key not in deduped:
            deduped[key] = record
            continue

        logger.warning(
            "Duplicate company name after normalization: %s",
            company_name,
        )
        deduped[key] = merge_records(deduped[key], record)

    return list(deduped.values())


def merge_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for field in FIELDNAMES:
        old_value = merged.get(field)
        new_value = incoming.get(field)

        if field == "partners":
            merged[field] = _merge_lists(old_value or [], new_value or [])
        elif _is_empty(old_value) and not _is_empty(new_value):
            merged[field] = new_value

    return merged


def _parse_partners(row: Any) -> list[str]:
    partners_cell = row.select_one("td.company-listing__list")
    if not partners_cell:
        return []

    partners = [clean_text(item) for item in partners_cell.select("li")]
    return [partner for partner in partners if partner]


def _current_stage_cell(row: Any) -> Any | None:
    for cell in row.find_all("td", recursive=False):
        classes = cell.get("class", [])
        if "u-lg-hide" in classes and "company-listing__list" not in classes:
            return cell
    return None


def _first_cell_with_class(row: Any, class_name: str) -> Any | None:
    for cell in row.find_all("td", recursive=False):
        if class_name in cell.get("class", []):
            return cell
    return None


def _merge_lists(existing: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*existing, *incoming]:
        normalized = normalize_company_name(value)
        if normalized and normalized not in seen:
            merged.append(value)
            seen.add(normalized)
    return merged


def _parse_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _parse_year(value: Any) -> int | None:
    number = _parse_positive_int(value)
    if number and 1900 <= number <= 2100:
        return number
    return None


def _is_empty(value: Any) -> bool:
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
        logger.exception("Sequoia scrape failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

