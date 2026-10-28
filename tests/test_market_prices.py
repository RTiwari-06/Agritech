from app_pages.market_prices import _comparison_rows


def test_comparison_rows_use_day_one_and_day_two_apmc_prices():
    rows = _comparison_rows(
        [
            {
                "name": "Onion - Local",
                "day1_price": 34.0,
                "day2_price": 35.5,
            }
        ]
    )

    assert rows == [
        {
            "Commodity": "Onion - Local",
            "Day 1 (₹/kg)": 34.0,
            "Day 2 (₹/kg)": 35.5,
        }
    ]


def test_report_upload_view_renders_market_history_without_streamlit_errors(monkeypatch):
    import app_pages.market_prices as market_prices
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(
        market_prices,
        "_api_get",
        lambda path, **kwargs: {
            "ok": True,
            "data": [
                {
                    "commodity": "Tomato",
                    "market": "Bengaluru",
                    "market_date": "2026-10-25",
                    "price_per_kg": 20.0,
                    "msp_per_kg": 15.0,
                    "arrival_metric_tonnes": 12.0,
                    "source": "report.csv",
                }
            ],
        },
    )
    at = AppTest.from_string(
        "import app_pages.market_prices as market_prices\nmarket_prices.app()"
    ).run()
    at.segmented_control(key="market_prices_view").set_value("Report uploads").run()

    assert not at.exception
    assert any("price and MSP history" in title.value for title in at.subheader)
    assert at.dataframe
