"""Shared helpers and mixin classes for the stonks-cli TUI."""

import logging
import math
from collections.abc import Callable
from typing import Any

from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message_pump import NoActiveAppError
from textual.widget import Widget
from textual.widgets import Static

from stonks_cli.market_session import SESSION_BADGE

logger = logging.getLogger(__name__)

_SHUTDOWN_ERRORS = {"App is not running", "no running event loop"}


def kv_row(container: Widget, label: str, value: str) -> None:
    """Mount a single label/value row into *container*."""
    row = Horizontal(classes="kv-row")
    container.mount(row)
    row.mount(Static(label, classes="kv-label"))
    row.mount(Static(value, classes="kv-value"))


def nice_yticks(values: list[float], n: int = 6) -> tuple[list[float], list[str]]:
    """Return (tick_values, tick_labels) with ~n rounded y-axis positions.

    Picks the "nice" step (1/2/5/10 × magnitude) closest to the raw step,
    so labels are always human-readable integers or short decimals.
    """
    if not values:
        return [], []
    if n < 2:
        raise ValueError(f"n must be at least 2, got {n}")
    lo, hi = min(values), max(values)
    if lo >= hi:
        v = round(lo, 2)
        return [v], [str(v)]
    raw_step = (hi - lo) / (n - 1)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = min(
        (f * magnitude for f in (1, 2, 5, 10)),
        key=lambda s: abs(s - raw_step),
    )
    lo_tick = math.floor(lo / step) * step
    hi_tick = math.ceil(hi / step) * step
    ticks: list[float] = []
    v = lo_tick
    while v <= hi_tick + step * 1e-9:
        ticks.append(round(v, 8))
        v += step
    if step >= 1:
        labels = [str(int(t)) for t in ticks]
    else:
        decimals = max(0, -math.floor(math.log10(step)))
        labels = [f"{t:.{decimals}f}" for t in ticks]
    return ticks, labels


def fmt_qty(qty: float) -> str:
    """Format a position quantity, dropping the decimal point for whole numbers."""
    return str(int(qty)) if float(qty).is_integer() else str(qty)


def fmt_chg(pct: float | None) -> str:
    """Format a pre-computed change percentage as a display string."""
    if pct is None:
        return "--"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def fmt_price(last: float | None, session: str) -> str:
    """Format the last-price cell with a session badge when applicable."""
    if last is None:
        return "N/A"
    badge = SESSION_BADGE.get(session, "")
    return f"{last:.2f} {badge}" if badge else f"{last:.2f}"


# ---------------------------------------------------------------------------
# Shared CSS for detail-style screens (StockDetailScreen, BacktestScreen)
# ---------------------------------------------------------------------------

DETAIL_SCREEN_CSS = """
.section-title {
    padding: 1 1 0 1;
    text-style: bold;
    color: $accent;
}
.summary-grid {
    height: auto;
    padding: 0 1;
}
.summary-col {
    width: 1fr;
    height: auto;
}
.kv-row {
    height: 1;
    padding: 0 1;
}
.kv-label {
    width: 30;
    color: $text-muted;
}
.kv-value {
    width: 1fr;
    text-style: bold;
}
#loading {
    height: 3;
    content-align: center middle;
}
#error-msg {
    padding: 1;
    color: $error;
}
"""


# ---------------------------------------------------------------------------
# Scroll navigation bindings and actions shared across detail screens
# ---------------------------------------------------------------------------

SCROLL_BINDINGS = [
    Binding("escape", "app.pop_screen", "Back"),
    Binding("q", "app.pop_screen", "Back", priority=True),
    Binding("up", "scroll_up", "Scroll Up", show=True),
    Binding("down", "scroll_down", "Scroll Down", show=True),
    Binding("pageup", "page_up", "Page Up", show=True),
    Binding("pagedown", "page_down", "Page Down", show=True),
]


class ScrollableScreenMixin:
    """Mixin providing scroll/page actions for a screen with a VerticalScroll.

    Subclasses must set ``_scroll_id`` (the CSS id of the VerticalScroll
    container, e.g. ``"detail-scroll"`` or ``"bt-scroll"``).
    """

    _scroll_id: str = "scroll"

    def _scroll(self) -> VerticalScroll:
        return self.query_one(f"#{self._scroll_id}", VerticalScroll)  # type: ignore[attr-defined]

    def action_scroll_up(self) -> None:
        self._scroll().scroll_up()

    def action_scroll_down(self) -> None:
        self._scroll().scroll_down()

    def action_page_up(self) -> None:
        self._scroll().scroll_page_up()

    def action_page_down(self) -> None:
        self._scroll().scroll_page_down()


class ThreadGuardMixin:
    """Mixin that provides a shutdown-safe ``call_from_thread`` helper.

    Worker threads may finish during app shutdown, at which point
    ``call_from_thread`` raises either ``RuntimeError("App is not running")``
    or ``NoActiveAppError``.  Both are treated as harmless no-ops.

    Works for both :class:`textual.app.App` subclasses (where ``self.app``
    returns ``self``) and :class:`textual.screen.Screen` subclasses.
    """

    def _call_from_thread_if_running(self, fn: Callable[..., Any], *args: Any) -> bool:
        """Call *fn* on the Textual event loop from a worker thread.

        Returns ``True`` if the call was dispatched, ``False`` if the app was
        already shutting down.
        """
        try:
            self.app.call_from_thread(fn, *args)  # type: ignore[attr-defined]
        except NoActiveAppError:
            logger.debug(
                "Skipping UI callback %s: no active app",
                getattr(fn, "__name__", repr(fn)),
            )
            return False
        except RuntimeError as exc:
            if not exc.args or exc.args[0] not in _SHUTDOWN_ERRORS:
                raise
            logger.debug(
                "Skipping UI callback %s: app is shutting down",
                getattr(fn, "__name__", repr(fn)),
            )
            return False
        return True
