from __future__ import annotations

import csv
import html
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable


def clean_text(value: Any) -> str | None:
    """Normalize HTML or string content into a compact text value."""
    if value is None:
        return None

    if hasattr(value, "get_text"):
        value = value.get_text(" ", strip=True)

    text = html.unescape(str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def normalize_company_name(name: str) -> str:
    """Create a stable dedupe key from a company name."""
    text = html.unescape(name or "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("&", "and")

    key = re.sub(r"[^a-z0-9]+", "", text)
    if key:
        return key

    return re.sub(r"\W+", "", text)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, records: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    field: _csv_value(record.get(field))
                    for field in fieldnames
                }
            )


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value if item is not None)
    return str(value)

