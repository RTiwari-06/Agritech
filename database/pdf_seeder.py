"""Extract APMC PDF price rows and seed Redis commodity hashes."""

from __future__ import annotations

import logging
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Pattern, Sequence, TypedDict

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from redis.exceptions import RedisError

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from database.redis_db import (  # noqa: E402
    PRODUCT_KEY_PREFIX,
    get_client,
    seed_default_stream,
)

logger = logging.getLogger(__name__)

PRICE_PER_QUINTAL = 100.0
DEFAULT_STOCK_KG = 500.0
DEFAULT_PDF_PATHS = (
    _REPO_ROOT / "data" / "bengaluru_apmc_day2.pdf",
    _REPO_ROOT / "data" / "bengaluru_apmc.pdf",
)

_NUMBER = r"[\d,.]+"
_CELL = r"[^|\r\n]+"
_COMMODITY = r"[A-Za-z0-9 \t()\/,\-]+?"

# APMC reports commonly expose one column per line with pipe separators.
# Five price fields match current reports; an optional sixth numeric field is
# accepted for report variants with an additional numeric column.
_PIPE_ROW_RE: Pattern[str] = re.compile(
    rf"(?P<commodity>{_COMMODITY})\r?\n[ \t]*\|[ \t]*(?P<one_kg>{_NUMBER})"
    rf"\r?\n[ \t]*\|[ \t]*(?P<one_q>{_NUMBER})"
    rf"\r?\n[ \t]*\|[ \t]*(?P<max_q>{_NUMBER})"
    rf"\r?\n[ \t]*\|[ \t]*(?P<min_q>{_NUMBER})"
    rf"\r?\n[ \t]*\|[ \t]*(?P<previous>{_NUMBER})"
    rf"(?:\r?\n[ \t]*\|[ \t]*(?P<extra>{_NUMBER}))?"
    rf"\r?\n[ \t]*\|[ \t]*(?P<date>\d{{4}}-\d{{2}}-\d{{2}})",
    re.MULTILINE,
)

_PIPE_CANDIDATE_RE: Pattern[str] = re.compile(
    rf"(?P<commodity>{_COMMODITY})\r?\n[ \t]*\|[ \t]*(?P<one_kg>{_CELL})"
    rf"\r?\n[ \t]*\|[ \t]*(?P<one_q>{_CELL})"
    rf"\r?\n[ \t]*\|[ \t]*(?P<max_q>{_CELL})"
    rf"\r?\n[ \t]*\|[ \t]*(?P<min_q>{_CELL})"
    rf"\r?\n[ \t]*\|[ \t]*(?P<previous>{_CELL})"
    rf"(?:\r?\n[ \t]*\|[ \t]*(?P<extra>{_CELL}))?"
    rf"\r?\n[ \t]*\|[ \t]*(?P<date>[^\r\n|]+)",
    re.MULTILINE,
)

# Preserve compatibility with text-based tables in existing local APMC PDFs.
_TEXT_ROW_RE: Pattern[str] = re.compile(
    rf"^[ \t]*(?P<commodity>{_COMMODITY})[ \t]+"
    rf"(?P<one_kg>{_NUMBER})[ \t]+(?P<one_q>{_NUMBER})[ \t]+"
    rf"(?P<max_q>{_NUMBER})[ \t]+(?P<min_q>{_NUMBER})[ \t]+"
    rf"(?P<previous>{_NUMBER})"
    rf"(?:[ \t]+(?P<extra>{_NUMBER}))?[ \t]+"
    rf"(?P<date>\d{{4}}-\d{{2}}-\d{{2}})[ \t]*$",
    re.MULTILINE,
)

_TEXT_CANDIDATE_RE: Pattern[str] = re.compile(
    rf"^[ \t]*(?P<commodity>{_COMMODITY})[ \t]+"
    r"(?P<one_kg>\S+)[ \t]+(?P<one_q>\S+)[ \t]+"
    r"(?P<max_q>\S+)[ \t]+(?P<min_q>\S+)[ \t]+"
    r"(?P<previous>\S+)(?:[ \t]+(?P<extra>\S+))?[ \t]+"
    r"(?P<date>\S+)[ \t]*$",
    re.MULTILINE,
)

_FRUIT = (
    "mango", "banana", "apple", "grape", "orange", "papaya",
    "pomegranate", "guava", "sapota", "watermelon", "melon",
)
_VEGETABLE = (
    "onion", "potato", "tomato", "carrot", "cabbage", "cauliflower",
    "brinjal", "beans", "peas", "cucumber", "gourd", "spinach", "radish",
    "beetroot", "capsicum", "chilli", "garlic", "ginger",
)


class PriceRow(TypedDict):
    commodity: str
    date: str
    one_kg_price: float
    one_q_price: float
    max_price: float
    min_price: float
    previous_price: float
    price_per_kg: float
    modal_rs_per_quintal: float


class SeedRow(TypedDict):
    name: str
    category: str
    day1_price: float
    day2_price: float
    dynamic_price: float
    max_price: float
    min_price: float
    stock_kg: float
    capped: str


def _clean_number(value: Optional[str]) -> Optional[float]:
    """Parse a numeric PDF cell, tolerating thousands separators."""
    if value is None:
        return None
    try:
        number = float(value.replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _row_from_match(match: re.Match[str], source: Path) -> Optional[PriceRow]:
    commodity = (match.group("commodity") or "").strip()
    if not commodity or "commodity" in commodity.casefold() or "1kg price" in commodity.casefold():
        return None

    try:
        row_date = date.fromisoformat(match.group("date").strip()).isoformat()
    except (TypeError, ValueError):
        logger.warning("Skipping invalid APMC date in %s: %r", source, match.group("date"))
        return None

    one_kg_price = _clean_number(match.group("one_kg"))
    one_q_price = _clean_number(match.group("one_q"))
    max_price_q = _clean_number(match.group("max_q"))
    min_price_q = _clean_number(match.group("min_q"))
    previous_price_q = _clean_number(match.group("previous"))
    if any(
        value is None
        for value in (
            one_kg_price,
            one_q_price,
            max_price_q,
            min_price_q,
            previous_price_q,
        )
    ):
        logger.warning("Skipping invalid numeric APMC row for %r in %s", commodity, source)
        return None

    if (
        one_kg_price is None
        or one_q_price is None
        or max_price_q is None
        or min_price_q is None
        or previous_price_q is None
    ):
        return None

    return {
        "commodity": commodity,
        "date": row_date,
        "one_kg_price": one_kg_price,
        "one_q_price": one_q_price,
        "max_price": max_price_q / PRICE_PER_QUINTAL,
        "min_price": min_price_q / PRICE_PER_QUINTAL,
        "previous_price": previous_price_q / PRICE_PER_QUINTAL,
        "price_per_kg": one_kg_price,
        "modal_rs_per_quintal": one_q_price,
    }


def _parse_text(text: str, source: Path) -> List[PriceRow]:
    """Parse pipe blocks and plain text APMC rows from extracted PDF text."""
    rows: List[PriceRow] = []
    valid_pipe_spans = {match.span() for match in _PIPE_ROW_RE.finditer(text)}
    valid_text_spans = {match.span() for match in _TEXT_ROW_RE.finditer(text)}

    for candidate in _PIPE_CANDIDATE_RE.finditer(text):
        if candidate.span() in valid_pipe_spans:
            continue
        commodity = candidate.group("commodity").strip()
        if commodity and "commodity" not in commodity.casefold() and "1kg price" not in commodity.casefold():
            logger.warning(
                "Skipping malformed pipe-delimited APMC row for %r in %s",
                commodity,
                source,
            )

    for candidate in _TEXT_CANDIDATE_RE.finditer(text):
        if candidate.span() in valid_text_spans:
            continue
        commodity = candidate.group("commodity").strip()
        if commodity and "commodity" not in commodity.casefold() and "1kg price" not in commodity.casefold():
            logger.warning(
                "Skipping malformed text APMC row for %r in %s",
                commodity,
                source,
            )

    for pattern in (_PIPE_ROW_RE, _TEXT_ROW_RE):
        for match in pattern.finditer(text):
            try:
                row = _row_from_match(match, source)
            except (AttributeError, TypeError, ValueError, OverflowError) as exc:
                logger.warning("Skipping malformed APMC row in %s: %s", source, exc)
                continue
            if row:
                rows.append(row)

    if not rows:
        logger.warning("No valid APMC price rows found in %s", source)
    return rows


def extract_rows(pdf_path: Path) -> List[PriceRow]:
    """Extract and parse text from every page of an APMC PDF."""
    if not pdf_path.is_file():
        raise FileNotFoundError(f"APMC PDF not found: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    page_text: List[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text.append(page.extract_text() or "")
        except Exception as exc:
            logger.warning(
                "Could not extract text from page %d of %s: %s",
                page_number,
                pdf_path,
                exc,
            )
    return _parse_text("\n".join(page_text), pdf_path)


def _categorize(name: str) -> str:
    lowered = name.casefold()
    if any(word in lowered for word in _FRUIT):
        return "Fruit"
    if any(word in lowered for word in _VEGETABLE):
        return "Vegetable"
    return "Grain"


def build_seed_rows(pdf_paths: Sequence[Path]) -> List[SeedRow]:
    """Build one current Redis product row for each commodity."""
    by_commodity: Dict[str, List[PriceRow]] = {}
    for pdf_path in pdf_paths:
        for row in extract_rows(pdf_path):
            by_commodity.setdefault(row["commodity"], []).append(row)

    seed_rows: List[SeedRow] = []
    for commodity, rows in sorted(by_commodity.items(), key=lambda item: item[0].casefold()):
        rows.sort(key=lambda row: row["date"])
        latest = rows[-1]
        day2_price = latest["one_kg_price"]
        day1_price = (
            rows[-2]["one_kg_price"]
            if len(rows) > 1
            else latest["previous_price"]
        )
        seed_rows.append(
            {
                "name": commodity.strip(),
                "category": _categorize(commodity),
                "day1_price": day1_price,
                "day2_price": day2_price,
                "dynamic_price": day2_price,
                "max_price": latest["max_price"],
                "min_price": latest["min_price"],
                "stock_kg": DEFAULT_STOCK_KG,
                "capped": "false",
            }
        )
    return seed_rows


def default_pdf_path() -> Path:
    """Return the first existing default report in the documented order."""
    for path in DEFAULT_PDF_PATHS:
        if path.is_file():
            return path
    return DEFAULT_PDF_PATHS[0]


# Backward-compatible name used by earlier application entry points.
DEFAULT_PDF = default_pdf_path()


def seed_redis(pdf_paths: Sequence[Path], flush_db: bool = False) -> int:
    """Seed product hashes; preserve orders and chats unless explicitly asked."""
    seed_rows = build_seed_rows(pdf_paths)
    if not seed_rows:
        logger.error("No valid commodity rows found; Redis was not modified.")
        return 0

    client = get_client()
    if flush_db:
        client.flushdb()

    seeded = 0
    for product_id, row in enumerate(seed_rows, start=1):
        try:
            client.hset(
                f"{PRODUCT_KEY_PREFIX}{product_id}",
                mapping={
                    "id": product_id,
                    "name": row["name"],
                    "category": row["category"],
                    "day1_price": row["day1_price"],
                    "day2_price": row["day2_price"],
                    "dynamic_price": row["dynamic_price"],
                    "max_price": row["max_price"],
                    "min_price": row["min_price"],
                    "stock_kg": row["stock_kg"],
                    "capped": row["capped"],
                },
            )
            seeded += 1
        except (TypeError, ValueError, OverflowError) as exc:
            logger.warning(
                "Skipping invalid Redis row for product %r: %s",
                row["name"],
                exc,
            )
    # Spec §3.2/§3.3 + §5: seed the default live-stream fallback so
    # GET /api/streams is never empty after a report re-seed.
    seed_default_stream(client)
    return seeded


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point; accepts one optional PDF path and prints the count."""
    args = list(sys.argv[1:] if argv is None else argv)
    pdf_paths = [Path(args[0]).expanduser()] if args else [default_pdf_path()]
    try:
        seeded = seed_redis(pdf_paths, flush_db=False)
    except (OSError, ValueError, PdfReadError, RedisError) as exc:
        logger.error("APMC seeding failed: %s", exc)
        return 1
    print(
        f"Successfully seeded {seeded} products and stream defaults into Redis."
    )
    return 0 if seeded else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(main())
