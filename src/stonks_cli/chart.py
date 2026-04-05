"""Real-time interactive candlestick chart screen."""

from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import yfinance as yf
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Label, Static
from textual_plotext import PlotextPlot
from textual_plotext.plot import _themes

from stonks_cli.helpers import ThreadGuardMixin
from stonks_cli.helpers import nice_yticks as _nice_yticks

logger = logging.getLogger(__name__)

# Register a custom plotext theme with dark grid lines.
# Format: [canvas_color, axes_color, ticks_color, ticks_style, color_sequence]
_themes["stonks-chart"] = [
    "black",
    (50, 50, 50),
    (90, 90, 90),
    "default",
    ["green+", "red+", "blue+", "cyan+", "magenta+", "yellow", "orange"],
]

# (period, interval) tuples in zoom order -- widest to narrowest.
# yfinance requires specific interval/period combinations.
_ZOOM_LEVELS: list[tuple[str, str, str, int, int]] = [
    # (label,  period, interval, span_days, refresh_seconds)
    ("1D 1m", "1d", "1m", 1, 30),
    ("1D 2m", "1d", "2m", 1, 60),
    ("5D 5m", "5d", "5m", 5, 120),
    ("1M 15m", "1mo", "15m", 30, 300),
    ("3M 1h", "3mo", "1h", 90, 600),
    ("6M 1d", "6mo", "1d", 180, 1800),
    ("1Y 1d", "1y", "1d", 365, 1800),
    ("5Y 1wk", "5y", "1wk", 1825, 3600),
]
_DEFAULT_ZOOM = 2  # "5D 5m"

# Intervals for which yfinance can return data for arbitrary historical dates
# (centred-window fetches).  1h supports up to ~730 days back; daily/weekly
# are unlimited.
_HISTORICAL_INTERVALS = {"1h", "1d", "1wk"}

# Maximum days of history yfinance provides per interval (intraday only).
# Intervals absent from this dict have effectively unlimited history.
_INTERVAL_RETENTION_DAYS: dict[str, int] = {
    "1m": 7,
    "2m": 60,
    "5m": 60,
    "15m": 60,
    "1h": 730,
}

# Y-axis zoom: each up/down press multiplies/divides the range by this factor.
_Y_SCALE_STEP = 1.25


@dataclass
class _CandleData:
    """Parsed OHLCV candle data ready for display."""

    dates: list[str] = field(default_factory=list)
    opens: list[float] = field(default_factory=list)
    highs: list[float] = field(default_factory=list)
    lows: list[float] = field(default_factory=list)
    closes: list[float] = field(default_factory=list)
    volumes: list[float] = field(default_factory=list)
    bid: float | None = None
    ask: float | None = None
    last: float | None = None

    def __len__(self) -> int:
        return len(self.dates)


def _fetch_candles(
    symbol: str,
    period: str,
    interval: str,
    start: str | None = None,
    end: str | None = None,
) -> _CandleData:
    """Fetch OHLCV candle data from yfinance."""
    ticker = yf.Ticker(symbol)
    if start and end:
        hist = ticker.history(start=start, end=end, interval=interval)
    else:
        hist = ticker.history(period=period, interval=interval)

    data = _CandleData()
    if hist.empty:
        return data

    data.dates = [ts.strftime("%Y-%m-%d %H:%M") for ts in hist.index]
    data.opens = hist["Open"].astype(float).tolist()
    data.highs = hist["High"].astype(float).tolist()
    data.lows = hist["Low"].astype(float).tolist()
    data.closes = hist["Close"].astype(float).tolist()
    data.volumes = (
        hist["Volume"].astype(float).tolist()
        if "Volume" in hist.columns
        else [0.0] * len(hist)
    )

    if data.closes:
        data.last = data.closes[-1]

    return data


def _fetch_bid_ask(symbol: str) -> tuple[float | None, float | None]:
    """Fetch current bid/ask prices for *symbol* via ticker.info.

    This is a separate, slow call (yfinance scrapes a different endpoint)
    and should only be made on mount or zoom changes, not on every refresh.
    Returns (bid, ask), either of which may be None.
    """
    try:
        info = yf.Ticker(symbol).info
        return info.get("bid"), info.get("ask")
    except Exception:  # noqa: BLE001
        logger.debug("Failed to fetch bid/ask for %s", symbol)
        return None, None


_NAN = float("nan")

# Candle colours (RGB)
_COLOR_UP = (0, 200, 0)
_COLOR_DOWN = (200, 0, 0)


def _draw_candles(plt, data: _CandleData) -> None:
    """Draw candlesticks at sequential integer x positions.

    Each candle is a wick (high-low) and body (open-close).  To avoid
    plotext connecting adjacent candles, NaN sentinels break the line
    between every pair.
    """
    up_x: list[float] = []
    up_y: list[float] = []
    dn_x: list[float] = []
    dn_y: list[float] = []
    up_bx: list[float] = []
    up_by: list[float] = []
    dn_bx: list[float] = []
    dn_by: list[float] = []

    for i in range(len(data.dates)):
        op, cl = data.opens[i], data.closes[i]
        hi, lo = data.highs[i], data.lows[i]
        x = float(i)
        if cl >= op:
            up_x.extend([x, x, _NAN])
            up_y.extend([lo, hi, _NAN])
            up_bx.extend([x, x, _NAN])
            up_by.extend([op, cl, _NAN])
        else:
            dn_x.extend([x, x, _NAN])
            dn_y.extend([lo, hi, _NAN])
            dn_bx.extend([x, x, _NAN])
            dn_by.extend([op, cl, _NAN])

    if up_x:
        plt.plot(up_x, up_y, color=_COLOR_UP, marker="braille")
    if dn_x:
        plt.plot(dn_x, dn_y, color=_COLOR_DOWN, marker="braille")
    if up_bx:
        plt.plot(up_bx, up_by, color=_COLOR_UP, marker="hd")
    if dn_bx:
        plt.plot(dn_bx, dn_by, color=_COLOR_DOWN, marker="hd")


def _closest_date_index(dates: list[str], target: str) -> int:
    """Return the index of the date closest to *target* (bisect on sorted dates)."""
    idx = bisect.bisect_left(dates, target)
    if idx == 0:
        return 0
    if idx >= len(dates):
        return len(dates) - 1
    # Return whichever neighbour is lexicographically closer
    if (target >= dates[idx - 1]) and (target <= dates[idx]):
        return idx - 1 if (target < dates[idx]) else idx
    return idx


def _format_date_labels(dates: list[str], interval: str) -> list[str]:
    """Shorten date strings based on the zoom interval."""
    if interval in ("1m", "2m", "5m", "15m"):
        # MM-DD HH:MM
        return [d[5:16] for d in dates]
    if interval == "1h":
        # MM-DD HH:MM
        return [d[5:16] for d in dates]
    if interval == "1wk":
        return [d[:10] for d in dates]
    # Daily: YYYY-MM-DD
    return [d[:10] for d in dates]


class CandleChartScreen(ThreadGuardMixin, Screen):
    """Interactive candlestick chart for a single ticker."""

    is_modal = True

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Close"),
        Binding("q", "app.pop_screen", "Close"),
        Binding("left", "cursor_left", "< Prev"),
        Binding("right", "cursor_right", "Next >"),
        Binding("home", "cursor_home", "First", show=False),
        Binding("end", "cursor_end", "Last", show=False),
        Binding("plus,equals_sign", "zoom_in", "+Zoom"),
        Binding("minus,underscore", "zoom_out", "-Zoom"),
        Binding("up", "y_expand", "Y Wide"),
        Binding("down", "y_squeeze", "Y Narrow"),
    ]

    CSS = """
    CandleChartScreen {
        background: $surface;
    }
    #chart-title {
        padding: 0 1;
        text-style: bold;
        color: $accent;
    }
    #ohlc-bar {
        padding: 0 1;
        height: 1;
        color: $text;
    }
    #bid-ask-bar {
        padding: 0 1;
        height: 1;
        color: $text-muted;
    }
    #candle-chart {
        height: 1fr;
        border: solid $accent;
    }
    #zoom-label {
        padding: 0 1;
        height: 1;
        color: $text-muted;
        text-align: right;
    }
    """

    # Bid/ask is refreshed independently at this interval (seconds).
    # ticker.info is slow so we avoid fetching it on every candle refresh.
    _BID_ASK_REFRESH_SECS = 60

    def __init__(self, symbol: str) -> None:
        super().__init__()
        self._symbol = symbol.upper()
        self._zoom_idx = _DEFAULT_ZOOM
        self._data = _CandleData()
        self._cursor = -1  # -1 means "last candle"
        self._refresh_timer: Timer | None = None
        self._bid_ask_timer: Timer | None = None
        self._y_scale: float = 1.0  # multiplier on the natural price range
        self._prefetching: bool = False  # True while a history prefetch is in flight

    def compose(self) -> ComposeResult:
        yield Label(f"  {self._symbol} -- Candle Chart", id="chart-title")
        yield Static("", id="ohlc-bar")
        yield Static("", id="bid-ask-bar")
        yield PlotextPlot(id="candle-chart")
        yield Static("", id="zoom-label")
        yield Footer()

    def on_mount(self) -> None:
        self._load_data()
        self._restart_timer()
        self._refresh_bid_ask()
        self._bid_ask_timer = self.set_interval(
            self._BID_ASK_REFRESH_SECS, self._refresh_bid_ask
        )

    def on_resize(self) -> None:
        self._redraw()

    def _restart_timer(self) -> None:
        """(Re)start the auto-refresh timer for the current zoom level."""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        _, _, interval, _, refresh_secs = _ZOOM_LEVELS[self._zoom_idx]

        if interval in _HISTORICAL_INTERVALS:
            # Refresh centered on the current cursor so the view doesn't jump
            # when the user is looking at historical data.
            def _refresh() -> None:
                self._load_data(self._cursor_date())

            self._refresh_timer = self.set_interval(refresh_secs, _refresh)
        else:
            self._refresh_timer = self.set_interval(refresh_secs, self._load_data)

    @work(thread=True)
    def _refresh_bid_ask(self) -> None:
        """Fetch bid/ask in a background thread and update _data in place."""
        bid, ask = _fetch_bid_ask(self._symbol)
        self._call_from_thread_if_running(self._apply_bid_ask, bid, ask)

    def _apply_bid_ask(self, bid: float | None, ask: float | None) -> None:
        self._data.bid = bid
        self._data.ask = ask
        self._redraw()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    @work(thread=True, exclusive=True)
    def _load_data(self, zoom_target: str | None = None) -> None:
        # Snapshot the zoom index at call time so stale results from a previous
        # level can be discarded if a zoom happens while the fetch is in flight.
        zoom_idx = self._zoom_idx
        _, period, interval, span_days, _ = _ZOOM_LEVELS[zoom_idx]
        effective_target = zoom_target
        try:
            data = _CandleData()
            if zoom_target and interval in _HISTORICAL_INTERVALS:
                # Fetch a window centred on the cursor date so historical data
                # (e.g. 2022) is visible even on a "1Y" or "6M" zoom level.
                target_dt = datetime.strptime(zoom_target[:10], "%Y-%m-%d")
                half = timedelta(days=span_days // 2)
                start = (target_dt - half).strftime("%Y-%m-%d")
                end = (target_dt + half + timedelta(days=1)).strftime("%Y-%m-%d")
                data = _fetch_candles(
                    self._symbol, period, interval, start=start, end=end
                )
            if not data.dates:
                # Historical fetch failed (data outside retention window) or
                # not applicable -- fall back to the most-recent period, but
                # don't try to snap the cursor to the old historical date.
                effective_target = None
                data = _fetch_candles(self._symbol, period, interval)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch candles for %s: %s", self._symbol, exc)
            return
        self._call_from_thread_if_running(
            self._apply_data, data, effective_target, zoom_idx
        )

    def _apply_data(
        self,
        data: _CandleData,
        zoom_target: str | None = None,
        zoom_idx: int = -1,
    ) -> None:
        # Discard results that belong to a zoom level the user has since left.
        if zoom_idx >= 0 and zoom_idx != self._zoom_idx:
            return
        self._data = data
        # After a zoom, snap cursor to the closest date to the old position.
        if zoom_target and data.dates:
            self._cursor = _closest_date_index(data.dates, zoom_target)
        elif self._cursor >= len(data):
            self._cursor = len(data) - 1
        self._redraw()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _resolved_cursor(self) -> int:
        """Return the actual cursor index (resolve -1 to last candle)."""
        if not self._data:
            return 0
        if self._cursor < 0:
            return len(self._data) - 1
        return min(self._cursor, len(self._data) - 1)

    def _redraw(self) -> None:
        data = self._data
        if not data:
            return

        # --- OHLC info bar ---
        idx = self._resolved_cursor()
        ohlc_bar = self.query_one("#ohlc-bar", Static)
        if data.dates:
            op, hi, lo, cl = (
                data.opens[idx],
                data.highs[idx],
                data.lows[idx],
                data.closes[idx],
            )
            vol = data.volumes[idx]
            chg = cl - op
            chg_pct = (chg / op * 100) if op else 0
            sign = "+" if chg >= 0 else ""
            ohlc_text = (
                f"  {data.dates[idx]}  |  "
                f"O: {op:.2f}  H: {hi:.2f}  L: {lo:.2f}  C: {cl:.2f}  |  "
                f"Chg: {sign}{chg:.2f} ({sign}{chg_pct:.2f}%)  |  "
                f"Vol: {vol:,.0f}  "
                f"[{idx + 1}/{len(data)}]"
            )
            ohlc_bar.update(ohlc_text)
        else:
            ohlc_bar.update("  No data available")

        # --- Bid / Ask bar ---
        bid_ask_bar = self.query_one("#bid-ask-bar", Static)
        parts: list[str] = []
        if data.last is not None:
            parts.append(f"Last: {data.last:.2f}")
        if data.bid is not None:
            parts.append(f"Bid: {data.bid:.2f}")
        if data.ask is not None:
            parts.append(f"Ask: {data.ask:.2f}")
        if data.bid is not None and data.ask is not None:
            spread = data.ask - data.bid
            parts.append(f"Spread: {spread:.2f}")
        bid_ask_bar.update("  " + "  |  ".join(parts) if parts else "")

        # --- Zoom label ---
        zoom_entry = _ZOOM_LEVELS[self._zoom_idx]
        label, refresh_secs = zoom_entry[0], zoom_entry[4]
        refresh_str = (
            f"{refresh_secs}s" if refresh_secs < 60 else f"{refresh_secs // 60}m"
        )
        y_info = f"  Y:{self._y_scale:.2f}x" if self._y_scale != 1.0 else ""
        zoom_label = self.query_one("#zoom-label", Static)
        zoom_label.update(
            f"  [{label}  ~{refresh_str}]{y_info}"
            "  <-/-> navigate  |  +/- zoom  |  up/down y-axis  |  Home/End first/last"
        )

        # --- Candlestick chart ---
        chart = self.query_one("#candle-chart", PlotextPlot)
        plt = chart.plt
        plt.clear_data()
        plt.clear_figure()

        # Use our custom theme (registered at import time) so that
        # PlotextPlot.render() won't override colors.
        chart.theme = "stonks-chart"  # type: ignore[assignment]
        plt.grid(horizontal=True, vertical=True)

        if not data.dates:
            plt.title("No data")
            chart.refresh()
            return

        # Slice a window sized to 1 candle per 1.2 terminal columns.
        max_candles = max(10, int(chart.size.width / 1.2))
        n_total = len(data.dates)
        half_win = max_candles // 2
        win_start = max(0, idx - half_win)
        win_end = min(n_total, win_start + max_candles)
        win_start = max(0, win_end - max_candles)
        view = _CandleData(
            dates=data.dates[win_start:win_end],
            opens=data.opens[win_start:win_end],
            highs=data.highs[win_start:win_end],
            lows=data.lows[win_start:win_end],
            closes=data.closes[win_start:win_end],
            volumes=data.volumes[win_start:win_end],
        )
        cursor_in_view = idx - win_start
        n = len(view.dates)

        plt.xlim(-1, n)
        _draw_candles(plt, view)

        # Y-axis ticks -- apply _y_scale around the natural price midpoint.
        all_prices = view.highs + view.lows
        if all_prices and self._y_scale != 1.0:
            raw_lo, raw_hi = min(all_prices), max(all_prices)
            mid = (raw_lo + raw_hi) / 2
            half = (raw_hi - raw_lo) / 2 * self._y_scale
            ytick_vals, ytick_labels = _nice_yticks([max(0.0, mid - half), mid + half])
        else:
            ytick_vals, ytick_labels = _nice_yticks(all_prices)
        if ytick_vals:
            lo_tick = max(0.0, ytick_vals[0])
            plt.ylim(lo_tick, ytick_vals[-1])
            plt.yticks(ytick_vals, ytick_labels)

        # X-axis: show ~8 evenly spaced real date labels
        step = max(1, n // 8)
        tick_indices = list(range(0, n, step))
        tick_labels = _format_date_labels(
            [view.dates[i] for i in tick_indices],
            _ZOOM_LEVELS[self._zoom_idx][2],
        )
        plt.xticks(tick_indices, tick_labels)  # type: ignore[arg-type]

        # Draw cursor crosshair
        plt.vline(cursor_in_view, color="white")
        plt.hline(view.closes[cursor_in_view], color="white")

        plt.title(f"{self._symbol}")
        chart.refresh()

    # ------------------------------------------------------------------
    # Cursor movement
    # ------------------------------------------------------------------

    def action_cursor_left(self) -> None:
        if not self._data:
            return
        cur = self._resolved_cursor()
        if cur > 0:
            self._cursor = cur - 1
            self._redraw()
            if self._cursor == 0:
                self._maybe_prefetch_history()
        else:
            # Already at leftmost -- user gesture triggers a history load.
            self._maybe_prefetch_history()

    def action_cursor_right(self) -> None:
        if not self._data:
            return
        cur = self._resolved_cursor()
        if cur < len(self._data) - 1:
            self._cursor = cur + 1
            self._redraw()
            if self._cursor == len(self._data) - 1:
                self._maybe_prefetch_future()
        else:
            # Already at rightmost -- user gesture triggers a future load.
            self._maybe_prefetch_future()

    def action_cursor_home(self) -> None:
        if not self._data:
            return
        self._cursor = 0
        self._redraw()
        self._maybe_prefetch_history()

    def action_cursor_end(self) -> None:
        if not self._data:
            return
        self._cursor = -1  # track latest
        self._redraw()
        self._maybe_prefetch_future()

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def _cursor_date(self) -> str | None:
        """Return the date string at the current cursor, or None."""
        if not self._data or not self._data.dates:
            return None
        idx = self._resolved_cursor()
        return self._data.dates[idx]

    def _cursor_within_retention(self, new_idx: int) -> bool:
        """Return False if cursor is outside the new zoom level's retention window."""
        _, _, new_interval, _, _ = _ZOOM_LEVELS[new_idx]
        max_days = _INTERVAL_RETENTION_DAYS.get(new_interval)
        if max_days is None:
            return True  # unlimited history (daily / weekly)
        zoom_target = self._cursor_date()
        if not zoom_target:
            return True
        cursor_dt = datetime.strptime(zoom_target[:10], "%Y-%m-%d")
        return (datetime.now() - cursor_dt).days <= max_days

    def action_zoom_in(self) -> None:
        if self._zoom_idx > 0:
            new_idx = self._zoom_idx - 1
            if not self._cursor_within_retention(new_idx):
                return  # Historical data not available at this granularity
            zoom_target = self._cursor_date()
            self._zoom_idx = new_idx
            self._load_data(zoom_target)
            self._restart_timer()

    def action_zoom_out(self) -> None:
        if self._zoom_idx < len(_ZOOM_LEVELS) - 1:
            zoom_target = self._cursor_date()
            self._zoom_idx += 1
            self._load_data(zoom_target)
            self._restart_timer()

    # ------------------------------------------------------------------
    # Y-axis expand / squeeze
    # ------------------------------------------------------------------

    def action_y_expand(self) -> None:
        """Widen the visible price range (zoom out on the y-axis)."""
        self._y_scale *= _Y_SCALE_STEP
        self._redraw()

    def action_y_squeeze(self) -> None:
        """Narrow the visible price range (zoom in on the y-axis)."""
        self._y_scale = max(self._y_scale / _Y_SCALE_STEP, 0.05)
        self._redraw()

    # ------------------------------------------------------------------
    # History prefetch (scroll left past the loaded window)
    # ------------------------------------------------------------------

    def _maybe_prefetch_history(self) -> None:
        """Kick off a history prefetch if the current zoom supports it."""
        _, _, interval, _, _ = _ZOOM_LEVELS[self._zoom_idx]
        if interval not in _HISTORICAL_INTERVALS:
            return
        if self._prefetching:
            return
        self._prefetching = True
        self._prefetch_history()

    @work(thread=True)
    def _prefetch_history(self) -> None:
        """Fetch the span of candles immediately before the current first date."""
        _, period, interval, span_days, _ = _ZOOM_LEVELS[self._zoom_idx]
        if not self._data.dates:
            self._call_from_thread_if_running(setattr, self, "_prefetching", False)
            return
        first_date = self._data.dates[0]
        try:
            # yfinance end is exclusive, so use the first date directly as the
            # boundary -- this avoids a one-day gap in the fetched window.
            end_str = first_date[:10]
            end_dt = datetime.strptime(end_str, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=span_days)
            start_str = start_dt.strftime("%Y-%m-%d")
            new_data = _fetch_candles(
                self._symbol, period, interval, start=start_str, end=end_str
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("History prefetch failed for %s: %s", self._symbol, exc)
            self._call_from_thread_if_running(setattr, self, "_prefetching", False)
            return
        self._call_from_thread_if_running(self._prepend_data, new_data)

    def _prepend_data(self, new_data: _CandleData) -> None:
        """Prepend *new_data* candles that predate the current dataset."""
        self._prefetching = False
        if not new_data.dates or not self._data.dates:
            return
        current_first = self._data.dates[0]
        # Keep only candles strictly before the current first date (no duplicates).
        n_new = sum(1 for d in new_data.dates if d < current_first)
        if n_new == 0:
            return
        self._data.dates = new_data.dates[:n_new] + self._data.dates
        self._data.opens = new_data.opens[:n_new] + self._data.opens
        self._data.highs = new_data.highs[:n_new] + self._data.highs
        self._data.lows = new_data.lows[:n_new] + self._data.lows
        self._data.closes = new_data.closes[:n_new] + self._data.closes
        self._data.volumes = new_data.volumes[:n_new] + self._data.volumes
        # Shift the cursor so it still points at the same candle.
        if self._cursor >= 0:
            self._cursor += n_new
        self._redraw()

    def _maybe_prefetch_future(self) -> None:
        """Kick off a future prefetch if the current zoom supports it."""
        _, _, interval, _, _ = _ZOOM_LEVELS[self._zoom_idx]
        if interval not in _HISTORICAL_INTERVALS:
            return
        if self._prefetching:
            return
        self._prefetching = True
        self._prefetch_future()

    @work(thread=True)
    def _prefetch_future(self) -> None:
        """Fetch the span of candles immediately after the current last date."""
        _, period, interval, span_days, _ = _ZOOM_LEVELS[self._zoom_idx]
        if not self._data.dates:
            self._call_from_thread_if_running(setattr, self, "_prefetching", False)
            return
        last_date = self._data.dates[-1]
        try:
            # Use the last date as the inclusive start so intraday candles
            # remaining on that day are included; _append_data deduplicates.
            start_str = last_date[:10]
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
            end_str = (start_dt + timedelta(days=span_days)).strftime("%Y-%m-%d")
            new_data = _fetch_candles(
                self._symbol, period, interval, start=start_str, end=end_str
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Future prefetch failed for %s: %s", self._symbol, exc)
            self._call_from_thread_if_running(setattr, self, "_prefetching", False)
            return
        self._call_from_thread_if_running(self._append_data, new_data)

    def _append_data(self, new_data: _CandleData) -> None:
        """Append *new_data* candles that postdate the current dataset."""
        self._prefetching = False
        if not new_data.dates or not self._data.dates:
            return
        current_last = self._data.dates[-1]
        # Keep only candles strictly after the current last date (no duplicates).
        new_candles = [
            (d, o, h, lo, c, v)
            for d, o, h, lo, c, v in zip(
                new_data.dates,
                new_data.opens,
                new_data.highs,
                new_data.lows,
                new_data.closes,
                new_data.volumes,
            )
            if d > current_last
        ]
        if not new_candles:
            return
        dates, opens, highs, lows, closes, volumes = zip(*new_candles)
        self._data.dates += list(dates)
        self._data.opens += list(opens)
        self._data.highs += list(highs)
        self._data.lows += list(lows)
        self._data.closes += list(closes)
        self._data.volumes += list(volumes)
        self._redraw()
