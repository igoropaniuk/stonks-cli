"""Tests for stonks_cli.bond_fetcher: parsers, source fallback and caching."""

from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

from stonks_cli import bond_fetcher
from stonks_cli.bond_fetcher import (
    BondPriceFetcher,
    BondQuote,
    BondSource,
    interpolate_yield,
    is_treasury,
    model_price,
    parse_fedinvest_csv,
    parse_yield_curve_csv,
)
from stonks_cli.bonds import BondInfo

# Lines taken from the real FedInvest CSV for 2026-10-14.
FEDINVEST_OCT14 = """\
912797UK1,MARKET BASED BILL,0.0,10/15/2026,,0.000000,99.989444,0.000000
91282CLM1,MARKET BASED NOTE,0.03625,09/30/2031,,93.718750,93.687500,0.000000
91282CRN3,MARKET BASED NOTE,0.05,09/30/2031,,99.750000,99.750000,0.000000
91282CRD5,MARKET BASED FRN,0.0419941698,07/31/2028,,100.037709,100.028970,100.028970
912797ZZ9,MARKET BASED BILL,0.0,10/22/2026,,0.000000,0.000000,0.000000
"""
FEDINVEST_OCT13 = """\
91282CLM1,MARKET BASED NOTE,0.03625,09/30/2031,,93.900000,93.875000,93.875000
91282CRN3,MARKET BASED NOTE,0.05,09/30/2031,,100.000000,100.000000,100.000000
"""

# Header and rows in the format published on home.treasury.gov.
CURVE_HEADER = (
    'Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr",'
    '"2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"\n'
)
CURVE_CSV = (
    CURVE_HEADER
    + """\
10/14/2026,4.11,4.15,4.15,4.28,4.36,4.37,4.53,4.82,4.92,5.00,5.09,5.19,5.57,5.52
10/13/2026,4.10,4.15,4.15,4.26,4.32,4.34,4.48,4.79,4.90,5.00,5.11,5.21,5.60,5.56
"""
)

FORM_HTML = '<form><input type="hidden" name="_csrf" value="tok123" /></form>'

TODAY = date(2026, 10, 15)
SETTLE = date(2026, 10, 15)
UST = BondInfo(maturity=date(2031, 9, 30), coupon=5.0, cusip="91282CRN3")
# Treasury prefix but not in the FedInvest file -> falls back to the curve.
UST_UNLISTED = BondInfo(maturity=date(2031, 9, 30), coupon=5.0, cusip="912810ZZ9")
CORPORATE = BondInfo(maturity=date(2030, 6, 1), coupon=4.0, cusip="037833AK6")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    bond_fetcher.clear_cache()
    monkeypatch.setattr(bond_fetcher, "eastern_today", lambda: TODAY)
    yield
    bond_fetcher.clear_cache()


class FakeTreasury:
    """httpx transport serving FedInvest and yield-curve responses."""

    def __init__(
        self,
        fedinvest: dict[date, str] | None = None,
        curve: dict[int, str] | None = None,
        fedinvest_status: int = 200,
        curve_status: int = 200,
    ) -> None:
        self.fedinvest = fedinvest or {}
        self.curve = curve or {}
        self.fedinvest_status = fedinvest_status
        self.curve_status = curve_status
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("selectSecurityPriceDate"):
            return httpx.Response(self.fedinvest_status, text=FORM_HTML)
        if path.endswith("securityPriceDetail"):
            if self.fedinvest_status != 200:
                return httpx.Response(self.fedinvest_status)
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            assert form["_csrf"] == "tok123"
            day = date(
                int(form["priceDateYear"]),
                int(form["priceDateMonth"]),
                int(form["priceDateDay"]),
            )
            return httpx.Response(200, text=self.fedinvest.get(day, ""))
        if "daily-treasury-rates.csv" in path:
            if self.curve_status != 200:
                return httpx.Response(self.curve_status)
            year = int(request.url.params["field_tdr_date_value"])
            return httpx.Response(200, text=self.curve.get(year, ""))
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def count(self, fragment: str) -> int:
        return sum(fragment in r.url.path for r in self.requests)


def _default_treasury(**kwargs) -> FakeTreasury:
    return FakeTreasury(
        fedinvest={
            date(2026, 10, 14): FEDINVEST_OCT14,
            date(2026, 10, 13): FEDINVEST_OCT13,
        },
        curve={2026: CURVE_CSV},
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


class TestParseFedInvest:
    def test_prefers_end_of_day_then_sell(self):
        prices = parse_fedinvest_csv(FEDINVEST_OCT14)
        assert prices["91282CRD5"] == pytest.approx(100.028970)  # end of day
        assert prices["91282CRN3"] == pytest.approx(99.75)  # sell
        assert prices["912797UK1"] == pytest.approx(99.989444)  # sell, no buy

    def test_skips_rows_without_any_price(self):
        assert "912797ZZ9" not in parse_fedinvest_csv(FEDINVEST_OCT14)

    def test_empty_and_malformed(self):
        assert parse_fedinvest_csv("") == {}
        assert parse_fedinvest_csv("garbage\nA,B,C\n") == {}


class TestParseYieldCurve:
    def test_rows_newest_first_with_tenors_in_years(self):
        curves = parse_yield_curve_csv(CURVE_CSV)
        assert [d for d, _ in curves] == [date(2026, 10, 14), date(2026, 10, 13)]
        latest = curves[0][1]
        assert latest[1 / 12] == 4.11
        assert latest[1.5 / 12] == 4.15
        assert latest[5.0] == 5.00
        assert latest[30.0] == 5.52

    def test_blank_cells_are_skipped(self):
        text = 'Date,"1 Mo","2 Yr"\n10/14/2026,,4.82\n'
        assert parse_yield_curve_csv(text) == [(date(2026, 10, 14), {2.0: 4.82})]

    def test_empty(self):
        assert parse_yield_curve_csv("") == []


class TestInterpolation:
    CURVE = {1.0: 4.0, 3.0: 5.0}

    def test_linear(self):
        assert interpolate_yield(self.CURVE, 2.0) == pytest.approx(4.5)

    def test_clamped(self):
        assert interpolate_yield(self.CURVE, 0.1) == 4.0
        assert interpolate_yield(self.CURVE, 30.0) == 5.0

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            interpolate_yield({}, 1.0)

    def test_model_price_near_par_for_coupon_near_curve(self):
        curve = parse_yield_curve_csv(CURVE_CSV)[0][1]
        assert model_price(UST, curve, SETTLE) == pytest.approx(100.0, abs=0.1)


def test_is_treasury():
    assert is_treasury(UST)
    assert not is_treasury(CORPORATE)
    assert not is_treasury(BondInfo(maturity=date(2031, 1, 1), coupon=1.0))


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


class TestFetchQuotes:
    def test_manual_price_needs_no_network(self):
        manual = BondInfo(maturity=date(2031, 9, 30), coupon=5.0, price=100.47)
        fake = FakeTreasury()
        quotes = BondPriceFetcher(fake.client()).fetch_quotes({"B": manual}, SETTLE)
        assert quotes == {"B": BondQuote(100.47, None, BondSource.MANUAL, None)}
        assert fake.requests == []

    def test_manual_price_wins_over_cusip(self):
        manual = BondInfo(
            maturity=date(2031, 9, 30), coupon=5.0, cusip="91282CRN3", price=101.0
        )
        quotes = BondPriceFetcher(_default_treasury().client()).fetch_quotes(
            {"B": manual}, SETTLE
        )
        assert quotes["B"].source == BondSource.MANUAL

    def test_fedinvest_latest_and_previous_day(self):
        fake = _default_treasury()
        quotes = BondPriceFetcher(fake.client()).fetch_quotes({"B": UST}, SETTLE)
        assert quotes["B"] == BondQuote(
            99.75, 100.0, BondSource.FEDINVEST, date(2026, 10, 14)
        )
        # Today (Oct 15) is empty, so the fetcher walked back to Oct 14 and 13.
        assert fake.count("securityPriceDetail") == 3
        assert fake.count("selectSecurityPriceDate") == 1  # one CSRF token

    def test_walks_back_over_weekend(self):
        fake = FakeTreasury(
            fedinvest={
                date(2026, 10, 9): FEDINVEST_OCT14,
                date(2026, 10, 8): FEDINVEST_OCT13,
            }
        )
        quotes = BondPriceFetcher(fake.client()).fetch_quotes({"B": UST}, SETTLE)
        assert quotes["B"].as_of == date(2026, 10, 9)
        assert quotes["B"].prev_close == 100.0

    def test_unlisted_treasury_falls_back_to_curve(self):
        fake = _default_treasury()
        quotes = BondPriceFetcher(fake.client()).fetch_quotes(
            {"B": UST_UNLISTED}, SETTLE
        )
        q = quotes["B"]
        assert q.source == BondSource.CURVE
        assert q.as_of == date(2026, 10, 14)
        assert q.price == pytest.approx(100.0, abs=0.1)
        assert q.prev_close is not None

    def test_fedinvest_error_falls_back_to_curve(self):
        fake = _default_treasury(fedinvest_status=403)
        quotes = BondPriceFetcher(fake.client()).fetch_quotes({"B": UST}, SETTLE)
        assert quotes["B"].source == BondSource.CURVE

    def test_both_sources_down_omits_bond(self):
        fake = _default_treasury(fedinvest_status=500, curve_status=500)
        assert BondPriceFetcher(fake.client()).fetch_quotes({"B": UST}, SETTLE) == {}

    def test_non_treasury_without_price_is_not_fetched(self):
        fake = _default_treasury()
        quotes = BondPriceFetcher(fake.client()).fetch_quotes({"C": CORPORATE}, SETTLE)
        assert quotes == {}
        assert fake.requests == []

    def test_curve_uses_previous_year_in_early_january(self, monkeypatch):
        monkeypatch.setattr(bond_fetcher, "eastern_today", lambda: date(2027, 1, 2))
        jan = 'Date,"5 Yr"\n01/02/2027,5.10\n'
        fake = FakeTreasury(curve={2027: jan, 2026: CURVE_CSV})
        quotes = BondPriceFetcher(fake.client()).fetch_quotes(
            {"B": UST_UNLISTED}, date(2027, 1, 2)
        )
        q = quotes["B"]
        assert q.as_of == date(2027, 1, 2)
        assert q.prev_close is not None

    def test_responses_are_cached_across_fetchers(self):
        fake = _default_treasury()
        BondPriceFetcher(fake.client()).fetch_quotes({"B": UST}, SETTLE)
        first = len(fake.requests)
        BondPriceFetcher(fake.client()).fetch_quotes({"B": UST}, SETTLE)
        assert len(fake.requests) == first

    def test_cache_expires(self, monkeypatch):
        fake = _default_treasury()
        clock = [1000.0]
        monkeypatch.setattr(bond_fetcher.time, "monotonic", lambda: clock[0])
        BondPriceFetcher(fake.client()).fetch_quotes({"B": UST}, SETTLE)
        first = len(fake.requests)
        clock[0] += bond_fetcher.CACHE_TTL_SECONDS + 1
        BondPriceFetcher(fake.client()).fetch_quotes({"B": UST}, SETTLE)
        assert len(fake.requests) > first
