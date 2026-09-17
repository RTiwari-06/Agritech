"""Market data layer: parse Agmarknet-style reports into canonical records.

Supports the actual "Marketwise Price & Arrival Report" PDFs the team receives
from agmarknet.gov.in (multi-day price/arrival tables) plus CSV uploads.

Canonical record fields (matches the ``market_prices`` collection):

    commodity, commodity_group, market, market_date (ISO),
    price_per_kg (Rs./kg), msp_per_kg (Rs./kg),
    arrival_metric_tonnes (MT), synthetic (bool), source (str),
    report_generated_at (ISO | None), raw_text (str), parse_error (str | None)
"""

import csv
import io
import re
from datetime import datetime, timedelta, timezone

SEP_MONTHS = {
    month: i + 1
    for i, month in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )
}

_REPORT_RE = re.compile(
    r"Marketwise\s+Price\s+&\s+Arrival\s+Report\s*\(\s*(\d{2})-(\d{2})-(\d{4})\s*\)"
)
_GENERATED_RE = re.compile(
    r"Generated\s+on:\s*(\d{2})-(\d{2})-(\d{4})\s+(\d{2}):(\d{2})\s*([AP]M)"
)
_PRICE_DATE_RE = re.compile(
    r"[Pp]rice\s+on\s+(\d{1,2})\s+([A-Za-z]{3})[^0-9]*(\d{4})"
)
_ARRIVAL_DATE_RE = re.compile(
    r"[Aa]rrival\s+on\s+(\d{1,2})\s+([A-Za-z]{3})[^0-9]*(\d{4})"
)


class ParseResult:
    def __init__(self):
        self.records: list[dict] = []
        self.errors: list[dict] = []
        self.meta: dict = {}


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

_NUMERIC_RE = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


def to_float(value):
    """Parse a report cell into float, tolerating currency commas and flags.

    Returns None for missing placeholders, raises ValueError for junk.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s or s in ("-", "--", "N.A.", "NA", "N/A", "*", ""):
        return None
    s = s.replace(",", "").replace("\u20b9", "").replace("Rs.", "").replace("Rs", "")
    match = _NUMERIC_RE.search(s)
    if not match:
        raise ValueError(f"non-numeric cell: {value!r}")
    return float(match.group(0))


def parse_iso(d, m, y):
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def _split_date_token(raw, pattern):
    match = pattern.search(raw)
    if not match:
        return None
    day, month, year = int(match.group(1)), SEP_MONTHS.get(match.group(2), 1), int(match.group(3))
    return parse_iso(day, month, year)


# ---------------------------------------------------------------------------
# Metadata extraction from page text
# ---------------------------------------------------------------------------

def _extract_metadata(text, source):
    meta = {"source": source, "report_date": None, "generated_at": None}
    m = _REPORT_RE.search(text)
    if m:
        meta["report_date"] = parse_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    g = _GENERATED_RE.search(text)
    if g:
        try:
            hour = int(g.group(4)) % 12 + (12 if g.group(6) == "PM" else 0)
            dt = datetime(
                int(g.group(3)), int(g.group(2)), int(g.group(1)), hour, int(g.group(5)),
                tzinfo=timezone(timedelta(hours=5, minutes=30)),
            )
            meta["generated_at"] = dt.isoformat()
        except ValueError:
            pass
    return meta


def _find_header_row(table):
    """Locate the header row: contains the word Commodity plus Price/Arrival on."""
    for idx, row in enumerate(table):
        cells = [str(c or "").replace("\n", " ") for c in row]
        joined = " | ".join(cells)
        if "Commodity" in joined and ("rice on" in joined or "rrival on" in joined or "MSP" in joined):
            return idx, cells
    return None, None


def _records_from_table(table, header_idx, header_cells, meta, raw_page_text):
    records = []
    errors = []
    price_dates = []
    arrival_dates = []
    for cell in header_cells:
        d = _split_date_token(cell, _PRICE_DATE_RE)
        if d:
            price_dates.append(d)
        d = _split_date_token(cell, _ARRIVAL_DATE_RE)
        if d:
            arrival_dates.append(d)

    slots = max(len(price_dates), len(arrival_dates))
    for row in table[header_idx + 1:]:
        cells = [str(c or "").strip() for c in row]
        raw_line = " | ".join(cells)
        if not any(cells):
            continue
        group = cells[0] if len(cells) > 0 else ""
        commodity = cells[1] if len(cells) > 1 else ""
        if not commodity or "Source" in commodity or "http" in commodity:
            continue
        if len(cells) < 3:
            errors.append({"reason": "row too short", "raw_text": raw_line})
            continue

        try:
            msp_quintal = to_float(cells[2])
        except ValueError as exc:
            errors.append({"reason": f"bad MSP cell: {exc}", "raw_text": raw_line})
            msp_quintal = None
            continue

        price_cells = cells[3:3 + slots] if len(cells) >= 3 + slots else [""] * slots
        arrival_cells = cells[6:6 + slots] if len(cells) >= 6 + slots else [""] * slots

        row_failed = False
        for k in range(slots):
            market_date = price_dates[k] if k < len(price_dates) else (
                arrival_dates[k] if k < len(arrival_dates) else meta.get("report_date")
            )
            if not market_date:
                continue
            record = {
                "commodity": commodity,
                "commodity_group": group or "General",
                "market": "National Aggregate",
                "market_date": market_date,
                "price_per_kg": None,
                "msp_per_kg": msp_quintal / 100.0 if msp_quintal is not None else None,
                "arrival_metric_tonnes": None,
                "synthetic": False,
                "source": meta.get("source", ""),
                "report_generated_at": meta.get("generated_at"),
                "raw_text": raw_line,
                "parse_error": None,
            }
            try:
                price_quintal = to_float(
                    price_cells[k] if k < len(price_cells) else None
                )
                record["price_per_kg"] = (
                    price_quintal / 100.0 if price_quintal is not None else None
                )
            except ValueError as exc:
                record["parse_error"] = f"bad price cell: {exc}"
                record["raw_text"] = raw_line
                record["price_per_kg"] = None
                row_failed = True

            try:
                arrival = to_float(
                    arrival_cells[k] if k < len(arrival_cells) else None
                )
                record["arrival_metric_tonnes"] = arrival
            except ValueError as exc:
                record["parse_error"] = f"bad arrival cell: {exc}"
                record["raw_text"] = raw_line
                record["arrival_metric_tonnes"] = None
                row_failed = True

            if not row_failed:
                records.append(record)
        if row_failed:
            errors.append({"reason": "row contained non-numeric data cells", "raw_text": raw_line})
    return records, errors


# ---------------------------------------------------------------------------
# PDF parser
# ---------------------------------------------------------------------------

def parse_pdf(path, source=None):
    """Parse an Agmarknet Marketwise PDF. Returns a ParseResult.

    pdfplumber is an optional dependency — imported on demand.
    """
    result = ParseResult()
    result.meta = {"source": source or str(path), "report_date": None, "generated_at": None,
                   "engine": "pdfplumber"}
    source = source or str(path)
    try:
        import pdfplumber
    except ImportError:
        result.meta["engine"] = "none"
        result.errors.append({
            "reason": "pdfplumber not installed; install with 'pip install pdfplumber'",
            "raw_text": source,
        })
        return result

    page_texts = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                page_texts.append(text)
                if not result.meta["report_date"] or not result.meta["generated_at"]:
                    meta = _extract_metadata(text, source)
                    result.meta = {**result.meta, **{k: v for k, v in meta.items() if v is not None}}
                tables = page.extract_tables()
                for table in tables:
                    header_idx, header_cells = _find_header_row(table)
                    if header_idx is None:
                        result.errors.append({
                            "reason": "no recognizable header row in table",
                            "raw_text": "\n".join(
                                " | ".join(str(c or "") for c in row) for row in table[:4]
                            ),
                        })
                        continue
                    records, errors = _records_from_table(
                        table, header_idx, header_cells, result.meta, text
                    )
                    result.records.extend(records)
                    result.errors.extend(errors)
    except Exception as exc:
        result.errors.append({"reason": f"pdf parse failed: {exc}", "raw_text": source})

    result.meta["pages"] = len(page_texts)
    return result


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------

_CSV_ALIASES = {
    "commodity": ("commodity", "commodity_name"),
    "commodity_group": ("commodity_group", "group", "commodity group"),
    "market": ("market", "market_name", "apmc"),
    "market_date": ("market_date", "date", "arrival_date", "report_date"),
    "msp_per_kg": ("msp_per_kg", "msp_rs_per_kg"),
    "msp_rs_per_quintal": ("msp_rs_per_quintal", "msp", "msp_rs_quintal"),
    "price_per_kg": ("price_per_kg", "price_kg", "market_price_rs_per_kg"),
    "price_rs_per_quintal": ("price_rs_per_quintal", "price", "price_rs_quintal",
                              "market_price_rs_per_quintal"),
    "arrival_metric_tonnes": ("arrival_metric_tonnes", "arrival", "arrival_mt",
                              "arrival_tonnes"),
}


def _canonical_key(raw_header):
    key = re.sub(r"[^a-z0-9]", "_", raw_header.strip().lower()).strip("_")
    for canonical, aliases in _CSV_ALIASES.items():
        if key in aliases:
            return canonical
    return key


def _parse_csv_date(value):
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_csv(path_or_stream, source=None):
    """Parse a CSV market report into canonical records."""
    result = ParseResult()
    source = source or (getattr(path_or_stream, "name", None) or "csv")
    result.meta = {"source": source, "engine": "stdlib csv"}

    if hasattr(path_or_stream, "read"):
        text = path_or_stream.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
    else:
        with open(path_or_stream, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)

    if reader.fieldnames is None:
        result.errors.append({"reason": "empty CSV", "raw_text": ""})
        return result

    mapped_rows = []
    for row in reader:
        canonical = {}
        raw_parts = []
        for header in reader.fieldnames:
            raw_parts.append(f"{header}={row.get(header, '')}")
            canonical[_canonical_key(header)] = row.get(header, "")
        mapped_rows.append((canonical, " | ".join(raw_parts)))

    for canonical, raw_line in mapped_rows:
        commodity = (canonical.get("commodity") or "").strip()
        if not commodity:
            result.errors.append({"reason": "missing commodity", "raw_text": raw_line})
            continue

        try:
            price_quintal = to_float(canonical.get("price_rs_per_quintal"))
        except ValueError as exc:
            price_quintal = None
            result.errors.append({"reason": f"bad price cell: {exc}", "raw_text": raw_line})
        try:
            price_kg = to_float(canonical.get("price_per_kg"))
        except ValueError as exc:
            price_kg = None
            result.errors.append({"reason": f"bad price_per_kg cell: {exc}", "raw_text": raw_line})
        try:
            msp_kg = to_float(canonical.get("msp_per_kg"))
        except ValueError:
            msp_kg = None
        try:
            msp_quintal = to_float(canonical.get("msp_rs_per_quintal"))
        except ValueError:
            msp_quintal = None
        try:
            arrival = to_float(canonical.get("arrival_metric_tonnes"))
        except ValueError as exc:
            arrival = None
            result.errors.append({"reason": f"bad arrival cell: {exc}", "raw_text": raw_line})

        price = price_kg if price_kg is not None else (
            price_quintal / 100.0 if price_quintal is not None else None
        )
        msp = msp_kg if msp_kg is not None else (
            msp_quintal / 100.0 if msp_quintal is not None else None
        )
        market_date = _parse_csv_date(canonical.get("market_date"))

        record = {
            "commodity": commodity,
            "commodity_group": (canonical.get("commodity_group") or "General").strip(),
            "market": (canonical.get("market") or "National Aggregate").strip(),
            "market_date": market_date,
            "price_per_kg": price,
            "msp_per_kg": msp,
            "arrival_metric_tonnes": arrival,
            "synthetic": False,
            "source": source,
            "report_generated_at": canonical.get("report_generated_at") or None,
            "raw_text": raw_line,
            "parse_error": None,
        }
        if market_date is None:
            record["parse_error"] = "missing/unparseable market_date"
            result.errors.append({
                "reason": "missing/unparseable market_date", "raw_text": raw_line
            })
        else:
            result.records.append(record)

    return result


def parse_file(path):
    """Route a file to the right parser based on its extension."""
    name = str(path).lower()
    if name.endswith(".pdf"):
        return parse_pdf(path)
    return parse_csv(path, source=str(path))