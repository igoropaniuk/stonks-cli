"""Fixed-coupon bond terms and pricing math.

Everything here is pure arithmetic with no network access, so it can be
used by the models, the price fetcher, and the TUI alike.

Conventions follow the US Treasury market:

* Prices are *clean* prices quoted as a percent of par (``100.0`` = par).
* Accrued interest uses the actual/actual (ICMA) day count: the fraction of
  the current coupon period that has elapsed, measured in calendar days.
* Coupon dates are generated backwards from maturity in steps of
  ``12 / frequency`` months.  When maturity falls on the last day of a
  month, every coupon date does too (the end-of-month rule), so a note
  maturing on 30 September pays on 31 March and 30 September.
* Yields are annual rates compounded ``frequency`` times a year (the
  "bond-equivalent" yield Treasury publishes), expressed in percent.
"""

import calendar
from dataclasses import dataclass
from datetime import date

DEFAULT_FACE_VALUE = 1000.0
DEFAULT_FREQUENCY = 2
VALID_FREQUENCIES = (1, 2, 4, 12)

# Yield search bounds for :func:`yield_to_maturity`, in percent.
_YTM_LOW = -50.0
_YTM_HIGH = 200.0
_YTM_TOLERANCE = 1e-9
_YTM_MAX_ITERATIONS = 200


@dataclass
class BondInfo:
    """Static terms of a fixed-coupon bond plus an optional manual quote.

    Attributes:
        maturity: Date the principal is repaid.
        coupon: Annual coupon rate in percent (``5.0`` for a 5% note).
        face_value: Par amount of one bond in the position currency.
        frequency: Coupon payments per year (1, 2, 4 or 12).
        cusip: Nine-character CUSIP used to look up Treasury prices.
        price: Manual clean price in percent of par.  When set it overrides
            any fetched quote.
    """

    maturity: date
    coupon: float
    face_value: float = DEFAULT_FACE_VALUE
    frequency: int = DEFAULT_FREQUENCY
    cusip: str | None = None
    price: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.maturity, str):
            try:
                self.maturity = date.fromisoformat(self.maturity)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid bond maturity '{self.maturity}': expected YYYY-MM-DD"
                ) from exc
        if not isinstance(self.maturity, date):
            raise ValueError("Bond maturity must be a date (YYYY-MM-DD)")
        if self.coupon < 0:
            raise ValueError("Bond coupon cannot be negative")
        if self.face_value <= 0:
            raise ValueError("Bond face value must be positive")
        if self.frequency not in VALID_FREQUENCIES:
            allowed = ", ".join(str(f) for f in VALID_FREQUENCIES)
            raise ValueError(f"Bond frequency must be one of {allowed}")
        if self.price is not None and self.price <= 0:
            raise ValueError("Bond price must be positive")
        if self.cusip is not None:
            cusip = self.cusip.strip().upper()
            if len(cusip) != 9 or not cusip.isalnum():
                raise ValueError(
                    f"Invalid CUSIP '{self.cusip}': expected 9 letters or digits"
                )
            self.cusip = cusip

    @property
    def coupon_amount(self) -> float:
        """Cash paid per coupon for one bond."""
        return self.face_value * self.coupon / 100 / self.frequency


# ---------------------------------------------------------------------------
# Coupon schedule
# ---------------------------------------------------------------------------


def _is_month_end(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def _shift_months(anchor: date, months: int, month_end: bool) -> date:
    """Return *anchor* moved by *months*, clamping the day to the month length.

    With *month_end* set the result is always the last day of its month.
    """
    total = anchor.year * 12 + (anchor.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    day = last_day if month_end else min(anchor.day, last_day)
    return date(year, month, day)


def coupon_period(bond: BondInfo, settle: date) -> tuple[date, date]:
    """Return ``(previous_coupon, next_coupon)`` bracketing *settle*.

    *settle* must be before maturity.  The previous coupon date may be
    before the bond was issued; that only matters for the first period,
    which this module treats as a regular one.
    """
    step = 12 // bond.frequency
    month_end = _is_month_end(bond.maturity)
    k = 0
    next_coupon = bond.maturity
    while True:
        prev_coupon = _shift_months(bond.maturity, -step * (k + 1), month_end)
        if prev_coupon <= settle:
            return prev_coupon, next_coupon
        next_coupon = prev_coupon
        k += 1


def remaining_coupons(bond: BondInfo, settle: date) -> int:
    """Number of coupon payments strictly after *settle*, including maturity."""
    if settle >= bond.maturity:
        return 0
    step = 12 // bond.frequency
    month_end = _is_month_end(bond.maturity)
    count = 1
    while _shift_months(bond.maturity, -step * count, month_end) > settle:
        count += 1
    return count


# ---------------------------------------------------------------------------
# Accrued interest and valuation
# ---------------------------------------------------------------------------


def accrued_fraction(bond: BondInfo, settle: date) -> float:
    """Fraction of the current coupon period elapsed at *settle* (0 to 1)."""
    if settle >= bond.maturity:
        return 0.0
    prev_coupon, next_coupon = coupon_period(bond, settle)
    return (settle - prev_coupon).days / (next_coupon - prev_coupon).days


def accrued_interest(bond: BondInfo, settle: date) -> float:
    """Accrued interest for one bond at *settle*, in currency units."""
    return bond.coupon_amount * accrued_fraction(bond, settle)


def clean_value(bond: BondInfo, quantity: float, price: float) -> float:
    """Value of *quantity* bonds at a clean *price* in percent of par."""
    return quantity * bond.face_value * price / 100


# ---------------------------------------------------------------------------
# Price <-> yield
# ---------------------------------------------------------------------------


def price_from_yield(bond: BondInfo, yield_pct: float, settle: date) -> float:
    """Clean price in percent of par for an annual *yield_pct* at *settle*.

    Uses the standard street formula: every remaining cash flow is
    discounted at ``yield / frequency`` per period, with the first period
    shortened to the fraction still to run.
    """
    if settle >= bond.maturity:
        return 100.0
    f = bond.frequency
    coupon = bond.coupon / f  # per 100 of face
    rate = yield_pct / 100 / f
    n = remaining_coupons(bond, settle)
    w = 1.0 - accrued_fraction(bond, settle)  # periods until next coupon
    dirty = sum(coupon / (1 + rate) ** (k + w) for k in range(n))
    dirty += 100 / (1 + rate) ** (n - 1 + w)
    return dirty - coupon * (1 - w)


def yield_to_maturity(bond: BondInfo, price: float, settle: date) -> float | None:
    """Annual yield in percent that reproduces a clean *price*.

    Solved by bisection, since price falls monotonically as yield rises.
    Returns ``None`` at or after maturity, or if no yield in a wide
    range fits the price.
    """
    if settle >= bond.maturity or price <= 0:
        return None
    lo, hi = _YTM_LOW, _YTM_HIGH
    if not (
        price_from_yield(bond, hi, settle)
        <= price
        <= price_from_yield(bond, lo, settle)
    ):
        return None
    for _ in range(_YTM_MAX_ITERATIONS):
        mid = (lo + hi) / 2
        if price_from_yield(bond, mid, settle) > price:
            lo = mid
        else:
            hi = mid
        if hi - lo < _YTM_TOLERANCE:
            break
    return (lo + hi) / 2


def years_to_maturity(bond: BondInfo, settle: date) -> float:
    """Remaining life of the bond in years (0 once matured)."""
    return max((bond.maturity - settle).days, 0) / 365.25
