"""Tests for stonks_cli.show_detail (format_detail and helpers)."""

from stonks_cli.show_detail import (
    _analyst_section,
    _eps_section,
    _kv_table,
    _performance_section,
    _revenue_section,
    _section,
    format_detail,
)
from stonks_cli.stock_detail import StockDetail

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_MINIMAL = StockDetail(
    symbol="AAPL",
    name="Apple Inc.",
    performance={
        "YTD Return": ("+ 8.14%", "- 3.08%"),
        "1-Year Return": ("+ 17.81%", "+ 18.16%"),
    },
    price_histories={},
    summary={
        "Previous Close": "150.00",
        "Open": "151.00",
        "Volume": "1,000,000",
    },
    eps_quarters=["Q1 FY25", "Q2 FY25"],
    eps_actual=[1.50, 1.60],
    eps_estimate=[1.45, 1.55],
    eps_diff=[0.05, 0.05],
    next_earnings_date="Apr 30, 2025",
    next_eps_estimate=1.70,
    rev_quarters=["Q1 FY25", "Q2 FY25"],
    rev_values=[90.0, 95.0],
    earn_values=[25.0, 27.0],
    price_targets={"low": 140.0, "mean": 180.0, "high": 220.0},
    recommendations=[],
    recommendation_key="buy",
    num_analysts=36,
    valuation={"Trailing P/E": "32.50"},
    financials={"Profit Margin": "25.50%"},
)

_EMPTY = StockDetail(
    symbol="UNKNOWN",
    name="UNKNOWN",
    performance={},
    price_histories={},
    summary={},
    eps_quarters=[],
    eps_actual=[],
    eps_estimate=[],
    eps_diff=[],
    next_earnings_date="N/A",
    next_eps_estimate=None,
    rev_quarters=[],
    rev_values=[],
    earn_values=[],
    price_targets={},
    recommendations=[],
    recommendation_key="N/A",
    num_analysts=0,
    valuation={},
    financials={},
)


# ---------------------------------------------------------------------------
# _section
# ---------------------------------------------------------------------------


class TestSection:
    def test_contains_title(self):
        out = _section("Summary")
        assert "Summary" in out

    def test_underline_matches_title_length(self):
        title = "Analyst Ratings"
        out = _section(title)
        lines = out.strip().split("\n")
        assert len(lines) == 2
        assert len(lines[1]) == len(title)

    def test_starts_with_newline(self):
        assert _section("X").startswith("\n")


# ---------------------------------------------------------------------------
# _kv_table
# ---------------------------------------------------------------------------


class TestKvTable:
    def test_empty_returns_empty_string(self):
        assert _kv_table([]) == ""

    def test_single_row(self):
        out = _kv_table([("Key", "Value")])
        assert "Key" in out
        assert "Value" in out

    def test_keys_aligned(self):
        rows = [("Short", "v1"), ("A Long Key", "v2")]
        out = _kv_table(rows)
        lines = out.split("\n")
        # Both lines should have the same length (keys padded to equal width)
        assert len(lines) == 2
        assert len(lines[0]) == len(lines[1])

    def test_default_indent_two_spaces(self):
        out = _kv_table([("K", "V")])
        assert out.startswith("  ")

    def test_custom_indent(self):
        out = _kv_table([("K", "V")], indent=4)
        assert out.startswith("    ")


# ---------------------------------------------------------------------------
# _performance_section
# ---------------------------------------------------------------------------


class TestPerformanceSection:
    def test_empty_performance_returns_empty(self):
        assert _performance_section(_EMPTY) == ""

    def test_contains_symbol(self):
        out = _performance_section(_MINIMAL)
        assert "AAPL" in out

    def test_contains_sp500_header(self):
        out = _performance_section(_MINIMAL)
        assert "S&P 500" in out

    def test_contains_all_labels(self):
        out = _performance_section(_MINIMAL)
        assert "YTD Return" in out
        assert "1-Year Return" in out

    def test_contains_return_values(self):
        out = _performance_section(_MINIMAL)
        assert "+ 8.14%" in out
        assert "- 3.08%" in out

    def test_contains_section_header(self):
        out = _performance_section(_MINIMAL)
        assert "Performance" in out


# ---------------------------------------------------------------------------
# _eps_section
# ---------------------------------------------------------------------------


class TestEpsSection:
    def test_empty_eps_returns_empty(self):
        assert _eps_section(_EMPTY) == ""

    def test_contains_section_header(self):
        out = _eps_section(_MINIMAL)
        assert "EPS" in out

    def test_contains_quarters(self):
        out = _eps_section(_MINIMAL)
        assert "Q1 FY25" in out
        assert "Q2 FY25" in out

    def test_actual_and_estimate_shown(self):
        out = _eps_section(_MINIMAL)
        assert "1.50" in out
        assert "1.45" in out

    def test_diff_shown_with_sign(self):
        out = _eps_section(_MINIMAL)
        assert "+0.05" in out

    def test_none_values_shown_as_na(self):
        detail = StockDetail(
            **{
                **_MINIMAL.__dict__,
                "eps_actual": [None, 1.60],
                "eps_estimate": [None, 1.55],
                "eps_diff": [None, 0.05],
            }
        )
        out = _eps_section(detail)
        assert "N/A" in out


# ---------------------------------------------------------------------------
# _revenue_section
# ---------------------------------------------------------------------------


class TestRevenueSection:
    def test_empty_revenue_returns_empty(self):
        assert _revenue_section(_EMPTY) == ""

    def test_contains_section_header(self):
        out = _revenue_section(_MINIMAL)
        assert "Revenue" in out

    def test_contains_quarters(self):
        out = _revenue_section(_MINIMAL)
        assert "Q1 FY25" in out
        assert "Q2 FY25" in out

    def test_revenue_values_shown(self):
        out = _revenue_section(_MINIMAL)
        assert "90.00" in out
        assert "95.00" in out

    def test_net_income_values_shown(self):
        out = _revenue_section(_MINIMAL)
        assert "25.00" in out
        assert "27.00" in out


# ---------------------------------------------------------------------------
# _analyst_section
# ---------------------------------------------------------------------------


class TestAnalystSection:
    def test_contains_recommendation(self):
        out = _analyst_section(_MINIMAL)
        assert "Buy" in out  # .title() applied

    def test_contains_analyst_count(self):
        out = _analyst_section(_MINIMAL)
        assert "36" in out

    def test_contains_price_targets(self):
        out = _analyst_section(_MINIMAL)
        assert "140.00" in out
        assert "180.00" in out
        assert "220.00" in out

    def test_section_header_present(self):
        out = _analyst_section(_MINIMAL)
        assert "Analyst Ratings" in out

    def test_unknown_key_title_case(self):
        detail = StockDetail(
            **{**_MINIMAL.__dict__, "recommendation_key": "strong buy"}
        )
        out = _analyst_section(detail)
        assert "Strong Buy" in out

    def test_missing_targets_omitted(self):
        detail = StockDetail(**{**_MINIMAL.__dict__, "price_targets": {}})
        out = _analyst_section(detail)
        assert "Target" not in out


# ---------------------------------------------------------------------------
# format_detail
# ---------------------------------------------------------------------------


class TestFormatDetail:
    def test_header_contains_symbol_and_name(self):
        out = format_detail(_MINIMAL)
        assert "AAPL" in out
        assert "Apple Inc." in out

    def test_all_sections_present_for_minimal(self):
        out = format_detail(_MINIMAL)
        assert "Summary" in out
        assert "Performance" in out
        assert "EPS" in out
        assert "Revenue" in out
        assert "Analyst Ratings" in out
        assert "Valuation" in out
        assert "Financials" in out

    def test_empty_detail_skips_optional_sections(self):
        out = format_detail(_EMPTY)
        assert "UNKNOWN" in out
        # No optional sections when data is empty
        assert "Performance" not in out
        assert "EPS" not in out
        assert "Revenue" not in out
        assert "Valuation" not in out
        assert "Financials" not in out

    def test_summary_key_values_present(self):
        out = format_detail(_MINIMAL)
        assert "Previous Close" in out
        assert "150.00" in out

    def test_valuation_section_shown_when_present(self):
        out = format_detail(_MINIMAL)
        assert "Trailing P/E" in out
        assert "32.50" in out

    def test_financials_section_shown_when_present(self):
        out = format_detail(_MINIMAL)
        assert "Profit Margin" in out
        assert "25.50%" in out

    def test_no_empty_lines_between_sections(self):
        """format_detail filters out empty parts."""
        out = format_detail(_EMPTY)
        # Should not have consecutive blank lines (empty strings filtered)
        assert "\n\n\n" not in out
