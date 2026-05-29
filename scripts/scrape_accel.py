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


BASE_URL = "https://www.accel.com"
SOURCE_URL = "https://www.accel.com/companies"
SANITY_QUERY_URL = "https://458oembh.api.sanity.io/v2023-05-03/data/query/production"
OUTPUT_DIR = PROJECT_ROOT / "data" / "accel"
JSON_OUTPUT_PATH = OUTPUT_DIR / "companies.json"
CSV_OUTPUT_PATH = OUTPUT_DIR / "companies.csv"
REQUEST_TIMEOUT_SECONDS = 30

SANITY_QUERY = """
*[_type == "company"] | order(name asc) {
  _id,
  name,
  slug,
  shortDescription,
  websiteUrl,
  twitter,
  firstInvestDate,
  initialInvestment->{name},
  initialInvestmentType,
  founders,
  partners[]->{name},
  sectorsForDisplay[]->{name},
  firstExitType,
  firstExitDescription,
  secondaryExitType,
  secondExitDescription,
  logo,
  featuredPhoto
}
"""

FIELDNAMES = [
    "company_name",
    "description",
    "current_stage",
    "partners",
    "founders",
    "sectors",
    "first_partnered_stage",
    "first_partnered_year",
    "first_partnered_date",
    "initial_investment_type",
    "exit_type",
    "exit_description",
    "company_profile_url",
    "company_url",
    "twitter_url",
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
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
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

    response = request_url(session, SANITY_QUERY_URL, params={"query": SANITY_QUERY})
    payload = response.json()
    raw_companies = payload.get("result")
    if not isinstance(raw_companies, list):
        raise RuntimeError("Accel Sanity response did not contain a result list")

    logger.info("Parsed %s raw Accel company records from Sanity", len(raw_companies))
    records = [
        normalize_company_record(company, scraped_at=scraped_at)
        for company in raw_companies
        if isinstance(company, dict)
    ]
    records = [record for record in records if record]

    deduped = dedupe_by_company_name(records)
    logger.info("Scraped %s Accel companies after dedupe", len(deduped))
    return deduped


def normalize_company_record(company: dict[str, Any], *, scraped_at: str) -> dict[str, Any] | None:
    company_name = clean_text(company.get("name"))
    if not company_name:
        logger.warning("Skipping Accel company with no name: %s", company.get("_id"))
        return None

    current_stage = clean_text((company.get("initialInvestment") or {}).get("name"))
    first_partnered_date = clean_text(company.get("firstInvestDate"))
    slug = slug_value(company.get("slug"))
    exit_type = clean_text(company.get("firstExitType")) or clean_text(company.get("secondaryExitType"))
    exit_description = clean_text(company.get("firstExitDescription")) or clean_text(company.get("secondExitDescription"))

    return {
        "company_name": company_name,
        "description": portable_text_to_text(company.get("shortDescription")),
        "current_stage": current_stage,
        "partners": names_from_refs(company.get("partners")),
        "founders": split_lines(portable_text_to_text(company.get("founders"), preserve_lines=True)),
        "sectors": names_from_refs(company.get("sectorsForDisplay")),
        "first_partnered_stage": current_stage,
        "first_partnered_year": normalize_year(first_partnered_date),
        "first_partnered_date": first_partnered_date,
        "initial_investment_type": clean_text(company.get("initialInvestmentType")),
        "exit_type": exit_type,
        "exit_description": exit_description,
        "company_profile_url": urljoin(BASE_URL, f"/companies/{slug}") if slug else None,
        "company_url": clean_text(company.get("websiteUrl")),
        "twitter_url": normalize_twitter_url(company.get("twitter")),
        "logo_url": sanity_image_url(company.get("logo")),
        "image_url": sanity_image_url(company.get("featuredPhoto")),
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


def portable_text_to_text(value: Any, *, preserve_lines: bool = False) -> str | None:
    if isinstance(value, str):
        if preserve_lines:
            return lines_to_text(value)
        return clean_text(value)

    if not isinstance(value, list):
        return clean_text(value)

    paragraphs: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            text = clean_text(block)
            if text:
                paragraphs.append(text)
            continue

        children = block.get("children")
        if not isinstance(children, list):
            continue

        child_text = "".join(str(child.get("text") or "") for child in children if isinstance(child, dict))
        text = child_text.strip()
        if text:
            paragraphs.append(text)

    if preserve_lines:
        return lines_to_text("\n".join(paragraphs))

    return clean_text(" ".join(paragraphs))


def lines_to_text(value: str) -> str | None:
    lines = [line for line in (clean_text(part) for part in value.splitlines()) if line]
    return "\n".join(lines) if lines else None


def names_from_refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    names: list[str] = []
    for item in value:
        if isinstance(item, dict):
            name = clean_text(item.get("name"))
        else:
            name = clean_text(item)
        if name:
            names.append(name)

    return merge_lists([], names)


def split_lines(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        item
        for item in (clean_text(part) for part in re.split(r"[\n;]+", value))
        if item
    ]


def slug_value(value: Any) -> str | None:
    if isinstance(value, dict):
        return clean_text(value.get("current"))
    return clean_text(value)


def normalize_twitter_url(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return f"https://x.com/{text.lstrip('@')}"


def sanity_image_url(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None

    asset = value.get("asset")
    if not isinstance(asset, dict):
        return None

    ref = clean_text(asset.get("_ref"))
    if not ref:
        return None

    match = re.match(r"^image-(?P<asset_id>.+)-(?P<dimensions>\d+x\d+)-(?P<extension>[a-z0-9]+)$", ref)
    if not match:
        return None

    return (
        "https://cdn.sanity.io/images/458oembh/production/"
        f"{match.group('asset_id')}-{match.group('dimensions')}.{match.group('extension')}"
    )


def dedupe_by_company_name(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for record in records:
        key = normalize_company_name(record.get("company_name", ""))
        if key not in deduped:
            deduped[key] = record
            continue

        logger.warning("Duplicate Accel company name after normalization: %s", record["company_name"])
        deduped[key] = merge_records(deduped[key], record)

    return list(deduped.values())


def merge_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for field in FIELDNAMES:
        old_value = merged.get(field)
        new_value = incoming.get(field)

        if field in {"partners", "founders", "sectors"}:
            merged[field] = merge_lists(old_value or [], new_value or [])
        elif is_empty(old_value) and not is_empty(new_value):
            merged[field] = new_value

    return merged


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
        logger.exception("Accel scrape failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
