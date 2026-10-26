"""Tests for bond support in the TUI: detail screen, form, and app actions."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from rich.text import Text
from textual.app import App
from textual.widgets import Button, DataTable, Input, Label, Select

from stonks_cli.app import PortfolioApp
from stonks_cli.bond_detail import BondDetailScreen, build_bond_rows
from stonks_cli.bond_fetcher import BondQuote, BondSource
from stonks_cli.bonds import BondInfo
from stonks_cli.dto import EquityResult
from stonks_cli.forms import EquityFormScreen
from stonks_cli.market import MarketSnapshot
from stonks_cli.models import Portfolio, Position

SETTLE = date(2026, 10, 15)
TERMS = BondInfo(maturity=date(2031, 9, 30), coupon=5.0, cusip="91282CRN3")
QUOTE = BondQuote(99.75, 100.0, BondSource.FEDINVEST, date(2026, 10, 14))
SYMBOL = "UST5.0SEP31"


def _bond_position() -> Position:
    return Position(SYMBOL, 15, 100.47, bond=TERMS)


def _rows_as_dict(rows) -> dict[str, str]:
    """Flatten build_bond_rows output to ``{label: plain text}``."""
    flat: dict[str, str] = {}
    for _, items in rows:
        for label, value in items:
            flat[label] = value.plain if isinstance(value, Text) else value
    return flat


# ---------------------------------------------------------------------------
# build_bond_rows
# ---------------------------------------------------------------------------


class TestBuildBondRows:
    def test_sections(self):
        rows = build_bond_rows(_bond_position(), QUOTE, SETTLE)
        assert [section for section, _ in rows] == [
            "Terms",
            "Position",
            "Pricing",
            "Value",
        ]

    def test_values_with_fedinvest_quote(self):
        info = _rows_as_dict(build_bond_rows(_bond_position(), QUOTE, SETTLE))
        assert info["CUSIP"] == "91282CRN3"
        assert info["Coupon"] == "5.000% paid semi-annually (25.00 USD per bond)"
        assert info["Maturity"] == "2031-09-30 (4.96 years)"
        assert info["Next coupon"] == "2027-03-31"
        assert info["Quantity"] == ("15 bonds x 1,000.00 USD face = 15,000.00 USD")
        assert info["Clean price"] == "99.7500 (% of par)"
        assert info["Source"] == (
            "U.S. Treasury FedInvest end-of-day price, as of 2026-10-14"
        )
        assert info["Previous close"] == "100.0000  (-0.2500)"
        assert info["Yield to maturity"] == "5.057%"
        assert info["Clean value"] == "14,962.50 USD"
        assert info["Accrued interest"] == "30.91 USD (2.06 per bond)"
        assert info["Market value"] == "14,993.41 USD"
        assert info["Unrealized P&L"] == "-108.00"

    def test_manual_quote_has_no_previous_close(self):
        manual = BondQuote(100.47, None, BondSource.MANUAL, None)
        info = _rows_as_dict(build_bond_rows(_bond_position(), manual, SETTLE))
        assert info["Source"] == "Manual price from the portfolio file"
        assert "Previous close" not in info

    def test_without_quote(self):
        info = _rows_as_dict(build_bond_rows(_bond_position(), None, SETTLE))
        assert info["Clean price"] == "N/A"
        assert info["Market value"] == "N/A"
        assert "manual price" in info["Source"]

    def test_matured_bond(self):
        info = _rows_as_dict(
            build_bond_rows(_bond_position(), QUOTE, date(2031, 10, 1))
        )
        assert info["Maturity"] == "2031-09-30 (matured)"
        assert "Next coupon" not in info
        assert info["Yield to maturity"] == "--"

    def test_rejects_non_bond(self):
        with pytest.raises(ValueError, match="not a bond"):
            build_bond_rows(Position("AAPL", 1, 1.0), None, SETTLE)


@pytest.mark.asyncio
async def test_detail_screen_closes_on_escape() -> None:
    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(BondDetailScreen(_bond_position(), QUOTE, SETTLE))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, BondDetailScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, BondDetailScreen)


# ---------------------------------------------------------------------------
# EquityFormScreen in bond mode
# ---------------------------------------------------------------------------


def _form_app(screen: EquityFormScreen, results: list) -> App:
    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(screen, results.append)

    return TestApp()


@pytest.mark.asyncio
async def test_bond_fields_hidden_until_bond_selected() -> None:
    results: list = []
    app = _form_app(EquityFormScreen(title="Add"), results)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.query_one("#bond-fields").display is False
        assert screen.query_one("#external-fields").display is True

        screen.query_one("#asset_type", Select).value = "bond"
        await pilot.pause()
        assert screen.query_one("#bond-fields").display is True
        assert screen.query_one("#external-fields").display is False
        assert "number of bonds" in str(screen.query_one("#qty-label", Label).render())
        assert "% of par" in str(screen.query_one("#avg-cost-label", Label).render())


@pytest.mark.asyncio
async def test_submit_bond_result() -> None:
    results: list = []
    app = _form_app(
        EquityFormScreen(title="Add", symbol=SYMBOL, qty="15", avg_cost="100.47"),
        results,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#asset_type", Select).value = "bond"
        await pilot.pause()
        screen.query_one("#maturity", Input).value = "2031-09-30"
        screen.query_one("#coupon", Input).value = "5"
        screen.query_one("#cusip", Input).value = "91282crn3"
        screen.query_one("#ok", Button).press()
        await pilot.pause()

    assert results == [
        EquityResult(
            symbol=SYMBOL,
            qty=15.0,
            avg_cost=100.47,
            currency="USD",
            asset_type="bond",
            external_id=None,
            bond={
                "maturity": date(2031, 9, 30),
                "coupon": 5.0,
                "face_value": 1000.0,
                "frequency": 2,
                "cusip": "91282CRN3",
                "price": None,
            },
        )
    ]


@pytest.mark.asyncio
async def test_blank_terms_keep_bond_etf_behaviour() -> None:
    results: list = []
    app = _form_app(
        EquityFormScreen(title="Add", symbol="TLT", qty="10", avg_cost="90"), results
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#asset_type", Select).value = "bond"
        await pilot.pause()
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

    assert len(results) == 1
    assert results[0]["asset_type"] == "bond"
    assert "bond" not in results[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("maturity", "30/09/2031", "Maturity must be a date"),
        ("coupon", "-1", "Coupon must be zero or a positive number"),
        ("face_value", "0", "Face value must be a positive number"),
        ("cusip", "123", "CUSIP must be 9 letters or digits"),
        ("bond_price", "abc", "Price must be a positive number"),
    ],
)
async def test_bond_field_validation(field: str, value: str, message: str) -> None:
    results: list = []
    app = _form_app(
        EquityFormScreen(
            title="Add", symbol=SYMBOL, qty="15", avg_cost="100", bond=TERMS
        ),
        results,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one(f"#{field}", Input).value = value
        screen.query_one("#ok", Button).press()
        await pilot.pause()
        error = str(screen.query_one("#error", Label).render())
    assert message in error
    assert results == []


@pytest.mark.asyncio
async def test_edit_form_prefills_bond_terms() -> None:
    terms = BondInfo(
        maturity=date(2031, 9, 30),
        coupon=5.0,
        face_value=100.0,
        frequency=4,
        cusip="91282CRN3",
        price=100.47,
    )
    app = _form_app(EquityFormScreen(title="Edit", bond=terms), [])
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.query_one("#asset_type", Select).value == "bond"
        assert screen.query_one("#bond-fields").display is True
        assert screen.query_one("#maturity", Input).value == "2031-09-30"
        assert screen.query_one("#coupon", Input).value == "5"
        assert screen.query_one("#face_value", Input).value == "100"
        assert screen.query_one("#frequency", Select).value == 4
        assert screen.query_one("#cusip", Input).value == "91282CRN3"
        assert screen.query_one("#bond_price", Input).value == "100.47"


# ---------------------------------------------------------------------------
# PortfolioApp with a bond position
# ---------------------------------------------------------------------------


def _bond_app(portfolio: Portfolio) -> PortfolioApp:
    app = PortfolioApp(portfolios=[portfolio])
    app._snap = MarketSnapshot(
        prices={SYMBOL: QUOTE.price},
        sessions={SYMBOL: "regular"},
        prev_closes={SYMBOL: QUOTE.prev_close or 0.0},
        exchange_codes={SYMBOL: "UST"},
        forex_rates={"USD": {"USD": 1.0}},
        bond_quotes={SYMBOL: QUOTE},
    )
    return app


@pytest.mark.asyncio
async def test_bond_row_shows_source_and_value() -> None:
    portfolio = Portfolio(positions=[_bond_position()])
    app = _bond_app(portfolio)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        row = [str(c) for c in table.get_row_at(0)]
    assert row[0] == SYMBOL
    assert row[1] == "UST"
    assert row[4] == "99.75"
    expected_value = _bond_position().market_value(99.75)
    assert row[7] == f"{expected_value:,.2f}"
    assert row[8] == "-108.00"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["f", "enter"])
async def test_detail_key_opens_bond_screen(key: str) -> None:
    app = _bond_app(Portfolio(positions=[_bond_position()]))
    with patch("stonks_cli.app.StockDetailScreen") as stock_detail:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one(DataTable).focus()
            await pilot.press(key)
            await pilot.pause()
            assert isinstance(app.screen, BondDetailScreen)
            assert app.screen._quote == QUOTE
    stock_detail.assert_not_called()


@pytest.mark.asyncio
async def test_chart_key_on_bond_notifies_instead() -> None:
    app = _bond_app(Portfolio(positions=[_bond_position()]))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.notify = MagicMock()  # type: ignore[method-assign]
        app.query_one(DataTable).focus()
        await pilot.press("g")
        await pilot.pause()
        assert not isinstance(app.screen, BondDetailScreen)
        assert app.screen is app.screen_stack[0]
    app.notify.assert_called_once()
    assert "aren't available for bonds" in app.notify.call_args.args[0]


@pytest.mark.asyncio
async def test_edit_bond_keeps_and_updates_terms() -> None:
    portfolio = Portfolio(positions=[_bond_position()])
    app = _bond_app(portfolio)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(DataTable).focus()
        await pilot.press("e")
        await pilot.pause()
        app.screen.query_one("#bond_price", Input).value = "101.25"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

    pos = portfolio.positions[0]
    assert pos.bond is not None
    assert pos.bond.price == 101.25
    assert pos.bond.cusip == "91282CRN3"
    assert pos.asset_type == "bond"


def test_news_symbols_exclude_bonds() -> None:
    portfolio = Portfolio(positions=[_bond_position(), Position("AAPL", 1, 100.0)])
    app = PortfolioApp(portfolios=[portfolio])
    assert app._collect_symbols() == ["AAPL"]
