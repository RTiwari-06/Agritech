"""Tests for APMC PDF parsing and Redis seed field defaults."""

from pathlib import Path

from database import pdf_seeder


def test_pipe_delimited_rows_clean_values_and_convert_quintal_bounds():
    text = (
        "Tomato - Local\n"
        "| 35.00\n"
        "| 3,500.00\n"
        "| 4,000.00\n"
        "| 3,000.00\n"
        "| 3,500.00\n"
        "| 500\n"
        "| 2026-09-24\n"
    )

    rows = pdf_seeder._parse_text(text, Path("sample.pdf"))

    assert len(rows) == 1
    row = rows[0]
    assert row["commodity"] == "Tomato - Local"
    assert row["one_kg_price"] == 35.0
    assert row["max_price"] == 40.0
    assert row["min_price"] == 30.0
    assert row["previous_price"] == 35.0


def test_parser_skips_headers_and_logs_bad_rows_without_losing_valid_rows(caplog):
    text = (
        "Commodity\n"
        "| 1KG Price\n"
        "| 1Q Price\n"
        "| Max Price\n"
        "| Min Price\n"
        "| Prev Price\n"
        "| 2026-09-24\n"
        "Broken Row\n"
        "| not-a-price\n"
        "| 2,000.00\n"
        "| 2,400.00\n"
        "| 1,000.00\n"
        "| 2,000.00\n"
        "| 2026-09-24\n"
        "Potato - Local\n"
        "| 20.00\n"
        "| 2,000.00\n"
        "| 2,400.00\n"
        "| 1,000.00\n"
        "| 2,000.00\n"
        "| 2026-09-24\n"
    )

    rows = pdf_seeder._parse_text(text, Path("sample.pdf"))

    assert len(rows) == 1
    assert rows[0]["commodity"] == "Potato - Local"
    assert rows[0]["min_price"] == 10.0
    assert "Skipping malformed pipe-delimited APMC row" in caplog.text


def test_existing_text_pdf_layout_is_still_supported():
    text = (
        "Commodity 1KG Price 1Q Price Max Price Min Price Prev Price Last Arrival\n"
        "Onion - Local 35.00 3,500.00 4,000.00 3,000.00 3,500.00 2026-09-24\n"
    )

    rows = pdf_seeder._parse_text(text, Path("sample.pdf"))

    assert len(rows) == 1
    assert rows[0]["commodity"] == "Onion - Local"
    assert rows[0]["previous_price"] == 35.0


def test_invalid_text_row_is_logged_and_valid_neighbor_is_parsed(caplog):
    text = (
        "Broken Row 10.00 bad 12.00 8.00 10.00 2026-09-24\n"
        "Onion - Local 35.00 3,500.00 4,000.00 3,000.00 3,500.00 2026-09-24\n"
    )

    rows = pdf_seeder._parse_text(text, Path("sample.pdf"))

    assert len(rows) == 1
    assert rows[0]["commodity"] == "Onion - Local"
    assert "Skipping malformed text APMC row" in caplog.text


def test_seed_rows_default_fields_to_required_values(monkeypatch):
    row = {
        "commodity": "Onion - Local",
        "date": "2026-09-24",
        "one_kg_price": 35.0,
        "one_q_price": 3500.0,
        "max_price": 40.0,
        "min_price": 30.0,
        "previous_price": 34.0,
        "price_per_kg": 35.0,
        "modal_rs_per_quintal": 3500.0,
    }
    monkeypatch.setattr(
        pdf_seeder,
        "extract_rows",
        lambda path: [row],
    )

    seed_rows = pdf_seeder.build_seed_rows([Path("sample.pdf")])

    assert seed_rows == [
        {
            "name": "Onion - Local",
            "category": "Vegetable",
            "day1_price": 34.0,
            "day2_price": 35.0,
            "dynamic_price": 35.0,
            "max_price": 40.0,
            "min_price": 30.0,
            "stock_kg": 500.0,
            "capped": "false",
        }
    ]


def test_default_pdf_fallback_uses_day2_before_base(monkeypatch):
    assert pdf_seeder.DEFAULT_PDF == pdf_seeder.default_pdf_path()
    existing = pdf_seeder.DEFAULT_PDF_PATHS[1]
    monkeypatch.setattr(
        pdf_seeder,
        "DEFAULT_PDF_PATHS",
        (Path("missing.pdf"), existing),
    )
    assert pdf_seeder.default_pdf_path() == existing
    assert pdf_seeder.default_pdf_path() == existing
