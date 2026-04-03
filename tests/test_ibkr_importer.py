"""Tests for stonks_cli.ibkr_importer."""

import textwrap
from pathlib import Path

import pytest

from stonks_cli.ibkr_importer import (
    IBKRImportError,
    _exchange_suffix,
    parse_ibkr_csv,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_csv(tmp_path: Path, content: str) -> Path:
    """Write *content* to a temp CSV file and return its path."""
    p = tmp_path / "positions.csv"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _exchange_suffix
# ---------------------------------------------------------------------------


class TestExchangeSuffix:
    def test_us_nasdaq_returns_none(self):
        assert _exchange_suffix("NASDAQ") is None

    def test_us_nyse_returns_none(self):
        assert _exchange_suffix("NYSE") is None

    def test_lse_returns_l(self):
        assert _exchange_suffix("LSE") == "L"

    def test_lseetf_returns_l(self):
        assert _exchange_suffix("LSEETF") == "L"

    def test_sbf_returns_pa(self):
        assert _exchange_suffix("SBF") == "PA"

    def test_aeb_returns_as(self):
        assert _exchange_suffix("AEB") == "AS"

    def test_ibis2_returns_de(self):
        assert _exchange_suffix("IBIS2") == "DE"

    def test_bvme_returns_mi(self):
        assert _exchange_suffix("BVME") == "MI"

    def test_bvme_etf_strips_suffix_and_returns_mi(self):
        assert _exchange_suffix("BVME.ETF") == "MI"

    def test_sbf_etf_strips_suffix_and_returns_pa(self):
        assert _exchange_suffix("SBF.ETF") == "PA"

    def test_unknown_exchange_returns_none(self):
        assert _exchange_suffix("UNKNOWN_XYZ") is None

    def test_case_insensitive(self):
        assert _exchange_suffix("lse") == "L"
        assert _exchange_suffix("Nasdaq") is None


# ---------------------------------------------------------------------------
# parse_ibkr_csv -- plain CSV format
# ---------------------------------------------------------------------------


class TestParsePlainCsv:
    def test_minimal_columns(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice
            AAPL,10,175.50
            """,
        )
        positions = parse_ibkr_csv(p)
        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"
        assert positions[0].quantity == pytest.approx(10.0)
        assert positions[0].avg_price == pytest.approx(175.50)
        assert positions[0].currency == "USD"

    def test_all_four_columns(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice,CurrencyPrimary,ListingExchange
            BP,200,452.30,GBP,LSE
            """,
        )
        positions = parse_ibkr_csv(p)
        assert len(positions) == 1
        pos = positions[0]
        assert pos.symbol == "BP.L"
        assert pos.quantity == pytest.approx(200.0)
        assert pos.avg_price == pytest.approx(452.30)
        assert pos.currency == "GBP"

    def test_listing_exchange_appends_suffix(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice,CurrencyPrimary,ListingExchange
            OR,15,368.40,EUR,SBF
            """,
        )
        assert parse_ibkr_csv(p)[0].symbol == "OR.PA"

    def test_us_exchange_no_suffix(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice,CurrencyPrimary,ListingExchange
            AAPL,10,175.50,USD,NASDAQ
            """,
        )
        assert parse_ibkr_csv(p)[0].symbol == "AAPL"

    def test_unknown_exchange_no_suffix(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice,CurrencyPrimary,ListingExchange
            XYZ,5,100.00,USD,SOMEEXCHANGE
            """,
        )
        assert parse_ibkr_csv(p)[0].symbol == "XYZ"

    def test_etf_exchange_code_suffix(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice,CurrencyPrimary,ListingExchange
            VWCE,50,120.00,EUR,BVME.ETF
            """,
        )
        assert parse_ibkr_csv(p)[0].symbol == "VWCE.MI"

    def test_currency_defaults_to_usd(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice
            MSFT,5,350.00
            """,
        )
        assert parse_ibkr_csv(p)[0].currency == "USD"

    def test_currency_uppercased(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice,CurrencyPrimary
            BP,100,450.00,gbp
            """,
        )
        assert parse_ibkr_csv(p)[0].currency == "GBP"

    def test_symbol_uppercased(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice
            aapl,10,175.00
            """,
        )
        assert parse_ibkr_csv(p)[0].symbol == "AAPL"

    def test_zero_quantity_skipped(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice
            AAPL,0,175.00
            MSFT,5,350.00
            """,
        )
        positions = parse_ibkr_csv(p)
        assert len(positions) == 1
        assert positions[0].symbol == "MSFT"

    def test_negative_quantity_skipped(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice
            AAPL,-10,175.00
            MSFT,5,350.00
            """,
        )
        positions = parse_ibkr_csv(p)
        assert len(positions) == 1
        assert positions[0].symbol == "MSFT"

    def test_non_equity_asset_class_skipped(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice,AssetClass
            AAPL,10,175.00,STK
            SPX_OPT,2,50.00,OPT
            EURUSD,1000,1.10,CASH
            """,
        )
        positions = parse_ibkr_csv(p)
        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"

    def test_stk_asset_class_included(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice,AssetClass
            AAPL,10,175.00,STK
            """,
        )
        assert len(parse_ibkr_csv(p)) == 1

    def test_no_asset_class_column_all_rows_included(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice
            AAPL,10,175.00
            MSFT,5,350.00
            """,
        )
        assert len(parse_ibkr_csv(p)) == 2

    def test_comma_in_number_parsed(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice
            AAPL,1000,1.50
            """,
        )
        assert parse_ibkr_csv(p)[0].quantity == pytest.approx(1000.0)

    def test_multiple_positions_all_parsed(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice,CurrencyPrimary,ListingExchange
            AAPL,10,175.50,USD,NASDAQ
            BP,200,452.30,GBP,LSE
            OR,15,368.40,EUR,SBF
            7203,100,2650.00,JPY,TSE
            """,
        )
        positions = parse_ibkr_csv(p)
        assert len(positions) == 4
        symbols = [pos.symbol for pos in positions]
        assert "AAPL" in symbols
        assert "BP.L" in symbols
        assert "OR.PA" in symbols
        assert "7203.T" in symbols

    def test_bom_handled(self, tmp_path):
        p = tmp_path / "positions.csv"
        p.write_bytes(b"\xef\xbb\xbfSymbol,Quantity,CostBasisPrice\nAAPL,10,175.00\n")
        positions = parse_ibkr_csv(p)
        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"

    def test_empty_rows_skipped(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice
            AAPL,10,175.00

            MSFT,5,350.00
            """,
        )
        assert len(parse_ibkr_csv(p)) == 2


# ---------------------------------------------------------------------------
# parse_ibkr_csv -- IBKR Flex Query multi-section format
# ---------------------------------------------------------------------------


class TestParseFlexCsv:
    def test_flex_format_parsed(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Open Positions,Header,Symbol,Quantity,CostBasisPrice,ListingExchange
            Open Positions,Data,AAPL,10,175.50,NASDAQ
            Open Positions,Data,BP,200,452.30,LSE
            Open Positions,Total,,,,,
            """,
        )
        positions = parse_ibkr_csv(p)
        assert len(positions) == 2
        symbols = [pos.symbol for pos in positions]
        assert "AAPL" in symbols
        assert "BP.L" in symbols

    def test_flex_total_row_ignored(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Open Positions,Header,Symbol,Quantity,CostBasisPrice
            Open Positions,Data,AAPL,10,175.50
            Open Positions,Total,,,
            """,
        )
        assert len(parse_ibkr_csv(p)) == 1

    def test_flex_non_equity_skipped(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Open Positions,Header,Symbol,Quantity,CostBasisPrice,AssetClass
            Open Positions,Data,AAPL,10,175.50,STK
            Open Positions,Data,SPX_OPT,2,50.00,OPT
            """,
        )
        positions = parse_ibkr_csv(p)
        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"

    def test_flex_missing_section_raises(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Trades,Header,Symbol,Quantity,Price
            Trades,Data,AAPL,10,175.50
            """,
        )
        with pytest.raises(IBKRImportError, match="Open Positions"):
            parse_ibkr_csv(p)


# ---------------------------------------------------------------------------
# parse_ibkr_csv -- error cases
# ---------------------------------------------------------------------------


class TestParseCsvErrors:
    def test_missing_symbol_column_raises(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Quantity,CostBasisPrice
            10,175.00
            """,
        )
        with pytest.raises(IBKRImportError, match="Symbol"):
            parse_ibkr_csv(p)

    def test_missing_quantity_column_raises(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,CostBasisPrice
            AAPL,175.00
            """,
        )
        with pytest.raises(IBKRImportError, match="Position"):
            parse_ibkr_csv(p)

    def test_missing_price_column_raises(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity
            AAPL,10
            """,
        )
        with pytest.raises(IBKRImportError, match="OpenPrice"):
            parse_ibkr_csv(p)

    def test_invalid_quantity_raises(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice
            AAPL,ten,175.00
            """,
        )
        with pytest.raises(IBKRImportError, match="Row 2"):
            parse_ibkr_csv(p)

    def test_invalid_price_raises(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice
            AAPL,10,n/a
            """,
        )
        with pytest.raises(IBKRImportError, match="Row 2"):
            parse_ibkr_csv(p)

    def test_zero_price_raises(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice
            AAPL,10,0
            """,
        )
        with pytest.raises(IBKRImportError, match="positive"):
            parse_ibkr_csv(p)

    def test_empty_file_raises(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("", encoding="utf-8")
        with pytest.raises(IBKRImportError, match="[Ee]mpty|empty"):
            parse_ibkr_csv(p)

    def test_nonexistent_file_raises(self, tmp_path):
        p = tmp_path / "missing.csv"
        with pytest.raises(IBKRImportError, match="Cannot read"):
            parse_ibkr_csv(p)

    def test_returns_empty_list_when_all_rows_skipped(self, tmp_path):
        p = write_csv(
            tmp_path,
            """\
            Symbol,Quantity,CostBasisPrice,AssetClass
            EURUSD,1000,1.10,CASH
            SPX_OPT,2,50.00,OPT
            """,
        )
        assert parse_ibkr_csv(p) == []
