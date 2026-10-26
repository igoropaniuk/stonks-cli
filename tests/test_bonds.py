"""Tests for stonks_cli.bonds: bond terms, coupon schedule and pricing math."""

from datetime import date

import pytest

from stonks_cli.bonds import (
    BondInfo,
    accrued_fraction,
    accrued_interest,
    clean_value,
    coupon_period,
    price_from_yield,
    remaining_coupons,
    years_to_maturity,
    yield_to_maturity,
)

# 5% U.S. Treasury note maturing 30 Sep 2031 (CUSIP 91282CRN3).
UST_2031 = BondInfo(maturity=date(2031, 9, 30), coupon=5.0, cusip="91282CRN3")
SETTLE = date(2026, 10, 15)


class TestBondInfo:
    def test_defaults(self):
        b = BondInfo(maturity=date(2030, 1, 15), coupon=4.0)
        assert b.face_value == 1000.0
        assert b.frequency == 2
        assert b.cusip is None
        assert b.price is None

    def test_maturity_string_is_parsed(self):
        b = BondInfo(maturity="2031-09-30", coupon=5.0)  # type: ignore[arg-type]
        assert b.maturity == date(2031, 9, 30)

    def test_bad_maturity_string_raises(self):
        with pytest.raises(ValueError, match="Invalid bond maturity"):
            BondInfo(maturity="30/09/2031", coupon=5.0)  # type: ignore[arg-type]

    def test_cusip_normalised(self):
        b = BondInfo(maturity=date(2031, 9, 30), coupon=5.0, cusip=" 91282crn3 ")
        assert b.cusip == "91282CRN3"

    @pytest.mark.parametrize("cusip", ["12345", "91282CRN3X", "91282-RN3"])
    def test_invalid_cusip_raises(self, cusip):
        with pytest.raises(ValueError, match="Invalid CUSIP"):
            BondInfo(maturity=date(2031, 9, 30), coupon=5.0, cusip=cusip)

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"coupon": -1.0}, "coupon cannot be negative"),
            ({"face_value": 0.0}, "face value must be positive"),
            ({"frequency": 3}, "frequency must be one of"),
            ({"price": 0.0}, "price must be positive"),
        ],
    )
    def test_validation(self, kwargs, message):
        args = {"maturity": date(2031, 9, 30), "coupon": 5.0, **kwargs}
        with pytest.raises(ValueError, match=message):
            BondInfo(**args)

    def test_zero_coupon_allowed(self):
        assert BondInfo(maturity=date(2030, 1, 1), coupon=0.0).coupon_amount == 0.0

    def test_coupon_amount(self):
        assert UST_2031.coupon_amount == pytest.approx(25.0)


class TestCouponSchedule:
    def test_month_end_rule(self):
        # Maturity on 30 Sep (a month end) pays on 31 Mar and 30 Sep.
        assert coupon_period(UST_2031, SETTLE) == (
            date(2026, 9, 30),
            date(2027, 3, 31),
        )

    def test_mid_month_maturity(self):
        b = BondInfo(maturity=date(2030, 2, 15), coupon=4.0)
        assert coupon_period(b, SETTLE) == (date(2026, 8, 15), date(2027, 2, 15))

    def test_leap_day_maturity_uses_month_end(self):
        b = BondInfo(maturity=date(2028, 2, 29), coupon=4.0)
        assert coupon_period(b, date(2027, 10, 1)) == (
            date(2027, 8, 31),
            date(2028, 2, 29),
        )

    def test_settle_on_coupon_date_starts_new_period(self):
        assert coupon_period(UST_2031, date(2027, 3, 31)) == (
            date(2027, 3, 31),
            date(2027, 9, 30),
        )

    def test_annual_frequency(self):
        b = BondInfo(maturity=date(2030, 6, 1), coupon=3.0, frequency=1)
        assert coupon_period(b, SETTLE) == (date(2026, 6, 1), date(2027, 6, 1))

    def test_remaining_coupons(self):
        # Mar 27 .. Sep 31 inclusive: 10 payments.
        assert remaining_coupons(UST_2031, SETTLE) == 10
        assert remaining_coupons(UST_2031, date(2031, 9, 29)) == 1
        assert remaining_coupons(UST_2031, date(2031, 9, 30)) == 0


class TestAccruedInterest:
    def test_accrued_fraction(self):
        # 15 days into a 182-day period.
        assert accrued_fraction(UST_2031, SETTLE) == pytest.approx(15 / 182)

    def test_accrued_interest_per_bond(self):
        assert accrued_interest(UST_2031, SETTLE) == pytest.approx(25 * 15 / 182)

    def test_zero_on_coupon_date(self):
        assert accrued_interest(UST_2031, date(2026, 9, 30)) == 0.0

    def test_zero_after_maturity(self):
        assert accrued_interest(UST_2031, date(2032, 1, 1)) == 0.0

    def test_clean_value(self):
        assert clean_value(UST_2031, 15, 100.47) == pytest.approx(15070.5)


class TestPriceYield:
    def test_par_when_yield_equals_coupon_on_coupon_date(self):
        assert price_from_yield(UST_2031, 5.0, date(2026, 9, 30)) == pytest.approx(
            100.0
        )

    def test_near_par_between_coupons(self):
        # Clean price stays close to par between coupons at yield == coupon.
        assert price_from_yield(UST_2031, 5.0, SETTLE) == pytest.approx(100.0, abs=0.01)

    def test_higher_yield_lowers_price(self):
        assert (
            price_from_yield(UST_2031, 6.0, SETTLE)
            < 100
            < price_from_yield(UST_2031, 4.0, SETTLE)
        )

    def test_known_value(self):
        # 10 remaining semi-annual coupons of 2.5 at 3% per period, from a
        # coupon date: annuity + discounted principal.
        settle = date(2026, 9, 30)
        r = 0.03
        expected = 2.5 * (1 - (1 + r) ** -10) / r + 100 / (1 + r) ** 10
        assert price_from_yield(UST_2031, 6.0, settle) == pytest.approx(expected)

    def test_matured_bond_prices_at_par(self):
        assert price_from_yield(UST_2031, 9.0, date(2031, 9, 30)) == 100.0

    @pytest.mark.parametrize("price", [92.5, 99.75, 100.0, 104.25])
    def test_yield_round_trip(self, price):
        ytm = yield_to_maturity(UST_2031, price, SETTLE)
        assert ytm is not None
        assert price_from_yield(UST_2031, ytm, SETTLE) == pytest.approx(price)

    def test_yield_at_fedinvest_price(self):
        ytm = yield_to_maturity(UST_2031, 99.75, SETTLE)
        assert ytm == pytest.approx(5.057, abs=0.001)

    def test_yield_none_after_maturity(self):
        assert yield_to_maturity(UST_2031, 100.0, date(2032, 1, 1)) is None

    def test_yield_none_for_impossible_price(self):
        assert yield_to_maturity(UST_2031, 10_000.0, SETTLE) is None

    def test_years_to_maturity(self):
        assert years_to_maturity(UST_2031, SETTLE) == pytest.approx(4.96, abs=0.01)
        assert years_to_maturity(UST_2031, date(2032, 1, 1)) == 0.0
