"""CLI formatted output for portfolio show command."""

from stonks_cli._columns import _TABLE_COLUMNS
from stonks_cli._row_model import RowKind, build_row_data
from stonks_cli.market import MarketSnapshot
from stonks_cli.models import Portfolio, portfolio_total

_SESSION_BADGES = {"pre": " PRE", "post": " AH", "closed": " CLS"}


def _fmt_chg(pct: float | None) -> str:
    """Format a pre-computed change percentage as a display string."""
    if pct is None:
        return "--"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def _collect_rows(portfolio: Portfolio, snap: MarketSnapshot) -> list[tuple[str, ...]]:
    """Build plain-string row tuples for all portfolio items."""
    rates = snap.forex_rates.get(portfolio.base_currency, {})
    rows: list[tuple[str, ...]] = []

    for rd in build_row_data(
        portfolio,
        snap.prices,
        snap.sessions,
        snap.prev_closes,
        snap.exchange_codes,
        rates,
    ):
        badge = _SESSION_BADGES.get(rd.session, "")
        if rd.kind == RowKind.POSITION:
            assert rd.qty is not None and rd.avg_cost is not None
            instrument = f"{rd.symbol}{badge}"
            if rd.last is None:
                rows.append(
                    (
                        instrument,
                        rd.exchange,
                        str(rd.qty),
                        f"{rd.avg_cost:.2f}",
                        "N/A",
                        "--",
                        "N/A",
                        "N/A",
                    )
                )
            else:
                rows.append(
                    (
                        instrument,
                        rd.exchange,
                        str(rd.qty),
                        f"{rd.avg_cost:.2f}",
                        f"{rd.last:.2f}",
                        _fmt_chg(rd.chg_pct),
                        f"{rd.mkt_value:,.2f}",
                        f"{rd.pnl:+,.2f}",
                    )
                )
        elif rd.kind == RowKind.WATCHLIST:
            instrument = f"{rd.symbol}{badge}"
            rows.append(
                (
                    instrument,
                    rd.exchange,
                    "-",
                    "-",
                    "N/A" if rd.last is None else f"{rd.last:.2f}",
                    _fmt_chg(rd.chg_pct),
                    "-",
                    "-",
                )
            )
        else:  # CASH
            assert rd.qty is not None
            rate = rd.last if rd.last is not None else 1.0
            rows.append(
                (
                    f"{rd.symbol} Cash",
                    "-",
                    f"{rd.qty:,.2f}",
                    "1.00",
                    f"{rate:.4f}",
                    "--",
                    f"{rd.mkt_value:,.2f}" if rd.mkt_value is not None else "N/A",
                    "-",
                )
            )

    return rows


def _render_table(
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
    total_label: str,
    total_str: str,
) -> str:
    """Render a fixed-width text table with a right-aligned total line."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    lines = [header_line, "-" * len(header_line)]
    for row in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, col_widths)))

    # Align total value under the Mkt Value column.
    mkt_value_idx = headers.index("Mkt Value")
    pre_width = sum(col_widths[:mkt_value_idx]) + mkt_value_idx * 2
    lines.append(
        total_label
        + " "
        * (pre_width + col_widths[mkt_value_idx] - len(total_label) - len(total_str))
        + total_str
    )

    return "\n".join(lines)


def format_show_table(portfolio: Portfolio, snap: MarketSnapshot) -> str:
    """Build a plain-text table for a single portfolio."""
    rows = _collect_rows(portfolio, snap)
    rates = snap.forex_rates.get(portfolio.base_currency, {})
    total = portfolio_total(portfolio, snap.prices, rates)
    total_str = "N/A" if total is None else f"{total:,.2f}"
    return _render_table(
        _TABLE_COLUMNS,
        rows,
        f"Total ({portfolio.base_currency})",
        total_str,
    )
