"""Tests for the candlestick chart screen and helpers."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from stonks_cli.chart import (
    _Y_SCALE_STEP,
    _ZOOM_LEVELS,
    CandleChartScreen,
    _CandleData,
    _closest_date_index,
    _fetch_bid_ask,
    _fetch_candles,
    _format_date_labels,
    _nice_yticks,
)

# ---------------------------------------------------------------------------
# _nice_yticks
# ---------------------------------------------------------------------------


class TestNiceYticks:
    def test_empty_input(self):
        vals, labels = _nice_yticks([])
        assert vals == []
        assert labels == []

    def test_single_value(self):
        vals, labels = _nice_yticks([150.0])
        assert vals == [150.0]
        assert labels == ["150.0"]

    def test_equal_values(self):
        vals, labels = _nice_yticks([100.0, 100.0])
        assert vals == [100.0]

    def test_integer_step_labels(self):
        vals, labels = _nice_yticks([100.0, 200.0])
        assert all(v >= 100.0 for v in vals)
        assert all(v <= 200.0 for v in vals)
        # Integer step -> labels should be integer strings
        assert all("." not in lbl for lbl in labels)

    def test_small_values_use_decimals(self):
        vals, labels = _nice_yticks([1.01, 1.09])
        assert len(vals) > 0
        assert any("." in lbl for lbl in labels)

    def test_returns_monotonically_increasing(self):
        vals, _ = _nice_yticks([50.0, 150.0, 100.0, 80.0])
        assert vals == sorted(vals)

    def test_clamps_to_positive_when_used_with_max(self):
        # Caller passes max(0, mid-half) -- verify _nice_yticks handles 0.0 input
        vals, _ = _nice_yticks([0.0, 100.0])
        assert vals[0] >= 0.0


# ---------------------------------------------------------------------------
# _closest_date_index
# ---------------------------------------------------------------------------


class TestClosestDateIndex:
    DATES = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-05"]

    def test_exact_match(self):
        assert _closest_date_index(self.DATES, "2025-01-02") == 1

    def test_before_first(self):
        assert _closest_date_index(self.DATES, "2024-12-31") == 0

    def test_after_last(self):
        assert _closest_date_index(self.DATES, "2025-12-31") == 3

    def test_between_values_returns_lower(self):
        # "2025-01-04" is between index 2 and 3; bisect gives 3, neighbour check
        result = _closest_date_index(self.DATES, "2025-01-04")
        assert result in (2, 3)

    def test_empty_list_returns_zero(self):
        # bisect on empty list returns 0, then idx==0 branch hits
        assert _closest_date_index([], "2025-01-01") == 0


# ---------------------------------------------------------------------------
# _format_date_labels
# ---------------------------------------------------------------------------


class TestFormatDateLabels:
    DATES = ["2025-03-15 09:30", "2025-03-15 14:00", "2025-04-01 00:00"]

    def test_intraday_formats(self):
        for interval in ("1m", "2m", "5m", "15m", "1h"):
            labels = _format_date_labels(self.DATES, interval)
            assert labels[0] == "03-15 09:30"

    def test_weekly_format(self):
        labels = _format_date_labels(self.DATES, "1wk")
        assert labels[0] == "2025-03-15"

    def test_daily_format(self):
        labels = _format_date_labels(self.DATES, "1d")
        assert labels[0] == "2025-03-15"


# ---------------------------------------------------------------------------
# _fetch_candles
# ---------------------------------------------------------------------------


class TestFetchCandles:
    @pytest.fixture(autouse=True)
    def _mock_yf(self):
        with patch("stonks_cli.chart.yf.Ticker") as mock_cls:
            self.mock_ticker = MagicMock()
            mock_cls.return_value = self.mock_ticker
            yield

    def _make_hist(self, n=3):
        idx = pd.date_range("2025-03-01", periods=n, freq="1min", tz="UTC")
        return pd.DataFrame(
            {
                "Open": [100.0] * n,
                "High": [105.0] * n,
                "Low": [98.0] * n,
                "Close": [102.0] * n,
                "Volume": [1000.0] * n,
            },
            index=idx,
        )

    def test_basic_fetch(self):
        self.mock_ticker.history.return_value = self._make_hist(3)

        data = _fetch_candles("AAPL", "1d", "1m")

        assert len(data) == 3
        assert data.bid is None  # bid/ask fetched separately via _fetch_bid_ask
        assert data.ask is None
        assert data.last == 102.0

    def test_empty_history_returns_empty(self):
        self.mock_ticker.history.return_value = pd.DataFrame()

        data = _fetch_candles("AAPL", "1d", "1m")

        assert len(data) == 0
        assert data.bid is None
        assert data.ask is None

    def test_missing_volume_defaults_to_zero(self):
        idx = pd.date_range("2025-03-01", periods=1, freq="1min", tz="UTC")
        hist = pd.DataFrame(
            {"Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5]},
            index=idx,
        )
        self.mock_ticker.history.return_value = hist

        data = _fetch_candles("AAPL", "1d", "1m")

        assert data.volumes[0] == 0.0

    def test_dates_formatted_correctly(self):
        self.mock_ticker.history.return_value = self._make_hist(1)

        data = _fetch_candles("AAPL", "1d", "1m")

        assert data.dates[0].startswith("2025-03-01")
        assert len(data.dates[0]) == 16  # "YYYY-MM-DD HH:MM"


class TestFetchBidAsk:
    def setup_method(self):
        patcher = patch("stonks_cli.chart.yf.Ticker")
        self.mock_yf = patcher.start()
        self.mock_ticker = MagicMock()
        self.mock_yf.return_value = self.mock_ticker
        self.addCleanup = patcher.stop

    def test_returns_bid_ask(self):
        self.mock_ticker.info = {"bid": 101.5, "ask": 102.5}
        bid, ask = _fetch_bid_ask("AAPL")
        assert bid == 101.5
        assert ask == 102.5

    def test_returns_none_on_exception(self):
        type(self.mock_ticker).info = property(
            lambda self: (_ for _ in ()).throw(Exception("network error"))
        )
        bid, ask = _fetch_bid_ask("AAPL")
        assert bid is None
        assert ask is None


# ---------------------------------------------------------------------------
# CandleChartScreen -- unit tests (no app)
# ---------------------------------------------------------------------------


class TestCandleChartScreenInit:
    def test_symbol_uppercased(self):
        screen = CandleChartScreen("nvda")
        assert screen._symbol == "NVDA"

    def test_default_zoom(self):
        screen = CandleChartScreen("AAPL")
        assert screen._zoom_idx == 2  # "5D 5m"

    def test_initial_cursor(self):
        screen = CandleChartScreen("AAPL")
        assert screen._cursor == -1

    def test_initial_y_scale(self):
        screen = CandleChartScreen("AAPL")
        assert screen._y_scale == 1.0

    def test_bindings_include_navigation(self):
        screen = CandleChartScreen("AAPL")
        keys = [b.key for b in screen.BINDINGS]
        assert "left" in keys
        assert "right" in keys
        assert "escape" in keys
        assert "up" in keys
        assert "down" in keys

    def test_refresh_timer_initially_none(self):
        screen = CandleChartScreen("AAPL")
        assert screen._refresh_timer is None


class TestCandleChartScreenCursor:
    def _make_screen(self, n=5) -> CandleChartScreen:
        screen = CandleChartScreen("AAPL")
        screen._data = _CandleData(
            dates=[f"2025-03-{i + 1:02d} 09:30" for i in range(n)],
            opens=[100.0] * n,
            highs=[105.0] * n,
            lows=[98.0] * n,
            closes=[102.0] * n,
            volumes=[1000.0] * n,
        )
        return screen

    def test_resolved_cursor_negative_returns_last(self):
        screen = self._make_screen(5)
        screen._cursor = -1
        assert screen._resolved_cursor() == 4

    def test_resolved_cursor_explicit(self):
        screen = self._make_screen(5)
        screen._cursor = 2
        assert screen._resolved_cursor() == 2

    def test_resolved_cursor_clamps_to_last(self):
        screen = self._make_screen(5)
        screen._cursor = 99
        assert screen._resolved_cursor() == 4

    def test_resolved_cursor_empty_data(self):
        screen = CandleChartScreen("AAPL")
        assert screen._resolved_cursor() == 0

    def test_action_cursor_left_decrements(self):
        screen = self._make_screen(5)
        screen._cursor = 3
        with patch.object(screen, "_redraw"):
            screen.action_cursor_left()
        assert screen._cursor == 2

    def test_action_cursor_left_at_start_no_change(self):
        screen = self._make_screen(5)
        screen._cursor = 0
        with patch.object(screen, "_redraw"):
            screen.action_cursor_left()
        assert screen._cursor == 0

    def test_action_cursor_right_increments(self):
        screen = self._make_screen(5)
        screen._cursor = 2
        with patch.object(screen, "_redraw"):
            screen.action_cursor_right()
        assert screen._cursor == 3

    def test_action_cursor_right_at_end_no_change(self):
        screen = self._make_screen(5)
        screen._cursor = 4
        with patch.object(screen, "_redraw"):
            screen.action_cursor_right()
        assert screen._cursor == 4

    def test_action_cursor_home(self):
        screen = self._make_screen(5)
        screen._cursor = 4
        with patch.object(screen, "_redraw"):
            screen.action_cursor_home()
        assert screen._cursor == 0

    def test_action_cursor_end(self):
        screen = self._make_screen(5)
        screen._cursor = 2
        with patch.object(screen, "_redraw"):
            screen.action_cursor_end()
        assert screen._cursor == -1

    def test_cursor_date_returns_date_string(self):
        screen = self._make_screen(5)
        screen._cursor = 2
        assert screen._cursor_date() == "2025-03-03 09:30"

    def test_cursor_date_empty_returns_none(self):
        screen = CandleChartScreen("AAPL")
        assert screen._cursor_date() is None


class TestCandleChartScreenZoom:
    def test_zoom_in_decrements_index(self):
        screen = CandleChartScreen("AAPL")
        screen._zoom_idx = 3
        screen._data = _CandleData(
            dates=["2026-03-15 09:30"],  # recent date, within 5m retention window
            opens=[100.0],
            highs=[105.0],
            lows=[98.0],
            closes=[102.0],
            volumes=[1000.0],
        )
        with patch.object(screen, "_load_data"), patch.object(screen, "_restart_timer"):
            screen.action_zoom_in()
        assert screen._zoom_idx == 2

    def test_zoom_in_blocked_outside_retention(self):
        """Zoom-in is blocked when cursor date exceeds the new interval's retention."""
        screen = CandleChartScreen("AAPL")
        screen._zoom_idx = 3  # 1M 15m; zoom-in targets 5D 5m (5m, 60-day retention)
        screen._data = _CandleData(
            dates=["2022-01-10 09:30"],  # ~4 years ago, outside 60-day retention
            opens=[100.0],
            highs=[105.0],
            lows=[98.0],
            closes=[102.0],
            volumes=[1000.0],
        )
        with (
            patch.object(screen, "_load_data") as mock_load,
            patch.object(screen, "_restart_timer"),
        ):
            screen.action_zoom_in()
        assert screen._zoom_idx == 3  # unchanged
        mock_load.assert_not_called()

    def test_zoom_in_at_minimum_no_change(self):
        screen = CandleChartScreen("AAPL")
        screen._zoom_idx = 0
        with patch.object(screen, "_load_data"), patch.object(screen, "_restart_timer"):
            screen.action_zoom_in()
        assert screen._zoom_idx == 0

    def test_zoom_out_increments_index(self):
        screen = CandleChartScreen("AAPL")
        screen._zoom_idx = 3
        screen._data = _CandleData(
            dates=["2025-03-01 09:30"],
            opens=[100.0],
            highs=[105.0],
            lows=[98.0],
            closes=[102.0],
            volumes=[1000.0],
        )
        with patch.object(screen, "_load_data"), patch.object(screen, "_restart_timer"):
            screen.action_zoom_out()
        assert screen._zoom_idx == 4

    def test_zoom_out_at_maximum_no_change(self):
        screen = CandleChartScreen("AAPL")
        screen._zoom_idx = len(_ZOOM_LEVELS) - 1
        with patch.object(screen, "_load_data"), patch.object(screen, "_restart_timer"):
            screen.action_zoom_out()
        assert screen._zoom_idx == len(_ZOOM_LEVELS) - 1

    def test_zoom_passes_cursor_date_to_load_data(self):
        screen = CandleChartScreen("AAPL")
        screen._zoom_idx = 3
        screen._data = _CandleData(
            dates=["2026-03-15 09:30"],
            opens=[100.0],
            highs=[105.0],
            lows=[98.0],
            closes=[102.0],
            volumes=[1000.0],
        )
        with (
            patch.object(screen, "_load_data") as mock_load,
            patch.object(screen, "_restart_timer"),
        ):
            screen.action_zoom_in()
        mock_load.assert_called_once_with("2026-03-15 09:30")

    def test_zoom_restarts_timer(self):
        screen = CandleChartScreen("AAPL")
        screen._zoom_idx = 3
        screen._data = _CandleData(
            dates=["2026-03-15 09:30"],
            opens=[100.0],
            highs=[105.0],
            lows=[98.0],
            closes=[102.0],
            volumes=[1000.0],
        )
        with (
            patch.object(screen, "_load_data"),
            patch.object(screen, "_restart_timer") as mock_restart,
        ):
            screen.action_zoom_in()
        mock_restart.assert_called_once()


class TestCandleChartScreenYScale:
    def test_y_expand_multiplies_scale(self):
        screen = CandleChartScreen("AAPL")
        with patch.object(screen, "_redraw"):
            screen.action_y_expand()
        assert screen._y_scale == pytest.approx(_Y_SCALE_STEP)

    def test_y_squeeze_divides_scale(self):
        screen = CandleChartScreen("AAPL")
        with patch.object(screen, "_redraw"):
            screen.action_y_squeeze()
        assert screen._y_scale == pytest.approx(1.0 / _Y_SCALE_STEP)

    def test_y_squeeze_floor(self):
        screen = CandleChartScreen("AAPL")
        screen._y_scale = 0.06
        with patch.object(screen, "_redraw"):
            screen.action_y_squeeze()
        assert screen._y_scale == pytest.approx(0.05)

    def test_y_expand_then_squeeze_returns_to_one(self):
        screen = CandleChartScreen("AAPL")
        with patch.object(screen, "_redraw"):
            screen.action_y_expand()
            screen.action_y_squeeze()
        assert screen._y_scale == pytest.approx(1.0)

    def test_y_expand_triggers_redraw(self):
        screen = CandleChartScreen("AAPL")
        with patch.object(screen, "_redraw") as mock_redraw:
            screen.action_y_expand()
        mock_redraw.assert_called_once()

    def test_y_squeeze_triggers_redraw(self):
        screen = CandleChartScreen("AAPL")
        with patch.object(screen, "_redraw") as mock_redraw:
            screen.action_y_squeeze()
        mock_redraw.assert_called_once()


class TestApplyData:
    def _make_screen(self) -> CandleChartScreen:
        screen = CandleChartScreen("AAPL")
        return screen

    def test_apply_data_stores_data(self):
        screen = self._make_screen()
        data = _CandleData(
            dates=["2025-03-01 09:30", "2025-03-02 09:30"],
            opens=[100.0, 101.0],
            highs=[105.0, 106.0],
            lows=[98.0, 99.0],
            closes=[102.0, 103.0],
            volumes=[1000.0, 2000.0],
        )
        with patch.object(screen, "_redraw"):
            screen._apply_data(data)
        assert screen._data is data

    def test_apply_data_snaps_cursor_to_zoom_target(self):
        screen = self._make_screen()
        data = _CandleData(
            dates=["2025-03-01 09:30", "2025-03-02 09:30", "2025-03-03 09:30"],
            opens=[100.0] * 3,
            highs=[105.0] * 3,
            lows=[98.0] * 3,
            closes=[102.0] * 3,
            volumes=[1000.0] * 3,
        )
        with patch.object(screen, "_redraw"):
            screen._apply_data(data, zoom_target="2025-03-02 09:30")
        assert screen._cursor == 1

    def test_apply_data_clamps_cursor_if_out_of_range(self):
        screen = self._make_screen()
        screen._cursor = 99
        data = _CandleData(
            dates=["2025-03-01 09:30", "2025-03-02 09:30"],
            opens=[100.0] * 2,
            highs=[105.0] * 2,
            lows=[98.0] * 2,
            closes=[102.0] * 2,
            volumes=[1000.0] * 2,
        )
        with patch.object(screen, "_redraw"):
            screen._apply_data(data)
        assert screen._cursor == 1

    def test_apply_data_discards_stale_zoom_idx(self):
        screen = self._make_screen()
        screen._zoom_idx = 4  # current zoom level
        data = _CandleData(
            dates=["2025-03-01 09:30"],
            opens=[100.0],
            highs=[105.0],
            lows=[98.0],
            closes=[102.0],
            volumes=[1000.0],
        )
        original_data = screen._data
        with patch.object(screen, "_redraw") as mock_redraw:
            # zoom_idx=3 does not match current _zoom_idx=4 -> discard
            screen._apply_data(data, zoom_idx=3)
        assert screen._data is original_data  # not replaced
        mock_redraw.assert_not_called()

    def test_apply_data_calls_redraw(self):
        screen = self._make_screen()
        data = _CandleData(
            dates=["2025-03-01 09:30"],
            opens=[100.0],
            highs=[105.0],
            lows=[98.0],
            closes=[102.0],
            volumes=[1000.0],
        )
        with patch.object(screen, "_redraw") as mock_redraw:
            screen._apply_data(data)
        mock_redraw.assert_called_once()


class TestZoomLevels:
    def test_all_levels_have_five_fields(self):
        for level in _ZOOM_LEVELS:
            assert len(level) == 5

    def test_refresh_seconds_decrease_with_zoom_in(self):
        """Finer zoom levels should refresh more frequently."""
        refresh_times = [level[4] for level in _ZOOM_LEVELS]
        assert refresh_times == sorted(refresh_times)

    def test_all_refresh_seconds_positive(self):
        for level in _ZOOM_LEVELS:
            assert level[4] > 0
