"""Unit tests for stonks_cli.helpers."""

import pytest
from textual.message_pump import NoActiveAppError

from stonks_cli.helpers import (
    ThreadGuardMixin,
    fmt_chg,
    fmt_price,
    fmt_qty,
    nice_yticks,
)
from stonks_cli.market_session import Session

# ---------------------------------------------------------------------------
# nice_yticks
# ---------------------------------------------------------------------------


def testnice_yticks_empty_input():
    assert nice_yticks([]) == ([], [])


def testnice_yticks_n_less_than_2_raises():
    with pytest.raises(ValueError, match="n must be at least 2"):
        nice_yticks([1.0, 2.0], n=1)


def testnice_yticks_single_value():
    ticks, labels = nice_yticks([42.7])
    assert ticks == [42.7]
    assert labels == ["42.7"]


def testnice_yticks_equal_lo_hi():
    ticks, labels = nice_yticks([5.0, 5.0])
    assert ticks == [5.0]
    assert labels == ["5.0"]


def testnice_yticks_integer_labels():
    ticks, labels = nice_yticks([100.0, 200.0, 150.0, 180.0])
    # All labels should be plain integers (no decimal point)
    assert all("." not in lbl for lbl in labels)
    assert ticks[0] <= 100.0
    assert ticks[-1] >= 200.0


def testnice_yticks_decimal_labels():
    ticks, labels = nice_yticks([0.1, 0.15, 0.12, 0.14])
    # With sub-1 step the labels should include a decimal point
    assert all("." in lbl for lbl in labels)
    assert ticks[0] <= 0.1
    assert ticks[-1] >= 0.15


def testnice_yticks_tick_count_near_n():
    ticks, labels = nice_yticks([0.0, 100.0], n=6)
    assert len(ticks) == len(labels)
    # Should produce roughly n ticks (within a factor of 2)
    assert 3 <= len(ticks) <= 12


def testnice_yticks_ticks_cover_range():
    values = [37.5, 42.1, 39.8, 41.0]
    ticks, _ = nice_yticks(values)
    assert ticks[0] <= min(values)
    assert ticks[-1] >= max(values)


# ---------------------------------------------------------------------------
# fmt_qty
# ---------------------------------------------------------------------------


def testfmt_qty_whole_number():
    assert fmt_qty(10.0) == "10"


def testfmt_qty_fractional():
    assert fmt_qty(10.5) == "10.5"


def testfmt_qty_integer_input():
    assert fmt_qty(7) == "7"


def testfmt_qty_large_whole():
    assert fmt_qty(1000.0) == "1000"


# ---------------------------------------------------------------------------
# fmt_chg
# ---------------------------------------------------------------------------


def testfmt_chg_none():
    assert fmt_chg(None) == "--"


def testfmt_chg_positive():
    assert fmt_chg(3.5) == "+3.50%"


def testfmt_chg_negative():
    assert fmt_chg(-1.25) == "-1.25%"


def testfmt_chg_zero():
    assert fmt_chg(0.0) == "+0.00%"


# ---------------------------------------------------------------------------
# fmt_price
# ---------------------------------------------------------------------------


def testfmt_price_none():
    assert fmt_price(None, Session.REGULAR) == "N/A"


def testfmt_price_regular_session():
    assert fmt_price(123.45, Session.REGULAR) == "123.45"


def testfmt_price_pre_market():
    result = fmt_price(99.99, Session.PRE)
    assert result == "99.99 PRE"


def testfmt_price_post_market():
    result = fmt_price(50.0, Session.POST)
    assert result == "50.00 AH"


def testfmt_price_closed():
    result = fmt_price(200.0, Session.CLOSED)
    assert result == "200.00 CLS"


# ---------------------------------------------------------------------------
# ThreadGuardMixin
# ---------------------------------------------------------------------------


class _ConcreteGuard(ThreadGuardMixin):
    """Minimal concrete class for testing the mixin."""

    def __init__(self, side_effect):
        self._side_effect = side_effect

    class _FakeApp:
        def __init__(self, side_effect):
            self._side_effect = side_effect

        def call_from_thread(self, fn, *args):
            if self._side_effect is not None:
                raise self._side_effect
            fn(*args)

    @property
    def app(self):
        return self._FakeApp(self._side_effect)


def test_thread_guard_success_returns_true():
    called_with = []
    guard = _ConcreteGuard(side_effect=None)
    result = guard._call_from_thread_if_running(called_with.append, 42)
    assert result is True
    assert called_with == [42]


def test_thread_guard_no_active_app_returns_false():
    guard = _ConcreteGuard(side_effect=NoActiveAppError())
    assert guard._call_from_thread_if_running(lambda: None) is False


def test_thread_guard_app_not_running_returns_false():
    guard = _ConcreteGuard(side_effect=RuntimeError("App is not running"))
    assert guard._call_from_thread_if_running(lambda: None) is False


def test_thread_guard_no_running_event_loop_returns_false():
    guard = _ConcreteGuard(side_effect=RuntimeError("no running event loop"))
    assert guard._call_from_thread_if_running(lambda: None) is False


def test_thread_guard_empty_runtime_error_reraises():
    # RuntimeError() with no args cannot be identified as a shutdown error -- re-raise
    guard = _ConcreteGuard(side_effect=RuntimeError())
    with pytest.raises(RuntimeError):
        guard._call_from_thread_if_running(lambda: None)


def test_thread_guard_unknown_runtime_error_reraises():
    guard = _ConcreteGuard(side_effect=RuntimeError("something unexpected"))
    with pytest.raises(RuntimeError, match="something unexpected"):
        guard._call_from_thread_if_running(lambda: None)


# ---------------------------------------------------------------------------
# stonks_cli.__version__ fallback
# ---------------------------------------------------------------------------


def test_version_fallback_when_package_not_installed():
    """__version__ falls back to '0.0.0.dev' when PackageNotFoundError is raised."""
    import importlib
    import importlib.metadata
    import unittest.mock
    from importlib.metadata import PackageNotFoundError

    import stonks_cli

    with unittest.mock.patch(
        "importlib.metadata.version", side_effect=PackageNotFoundError
    ):
        importlib.reload(stonks_cli)
        assert stonks_cli.__version__ == "0.0.0.dev"
    # Restore to real version
    importlib.reload(stonks_cli)
