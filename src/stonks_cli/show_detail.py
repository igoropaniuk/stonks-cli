"""CLI formatted output for the ``detail`` command."""

from itertools import zip_longest

from stonks_cli.stock_detail import StockDetail


def _section(title: str) -> str:
    return f"\n{title}\n{'-' * len(title)}"


def _kv_table(rows: list[tuple[str, str]], indent: int = 2) -> str:
    if not rows:
        return ""
    pad = " " * indent
    width = max(len(k) for k, _ in rows)
    return "\n".join(f"{pad}{k:<{width}}  {v}" for k, v in rows)


def _performance_section(detail: StockDetail) -> str:
    if not detail.performance:
        return ""
    col_w = max(max(len(k) for k in detail.performance), 6)
    sym_w = max(len(detail.symbol), 10)
    lines = [
        _section("Performance"),
        f"  {'Metric':<{col_w}}  {detail.symbol:<{sym_w}}  S&P 500",
        "  " + "-" * (col_w + sym_w + 11),
    ]
    for label, (stock_ret, sp_ret) in detail.performance.items():
        lines.append(f"  {label:<{col_w}}  {stock_ret:<{sym_w}}  {sp_ret}")
    return "\n".join(lines)


def _eps_section(detail: StockDetail) -> str:
    if not detail.eps_quarters:
        return ""
    lines = [
        _section("EPS (Quarterly)"),
        f"  {'Quarter':<10}  {'Actual':>8}  {'Estimate':>10}  {'Diff':>8}",
        "  " + "-" * 42,
    ]
    for q, actual, est, diff in zip_longest(
        detail.eps_quarters,
        detail.eps_actual,
        detail.eps_estimate,
        detail.eps_diff,
    ):
        act_s = f"{actual:.2f}" if actual is not None else "N/A"
        est_s = f"{est:.2f}" if est is not None else "N/A"
        dif_s = f"{diff:+.2f}" if diff is not None else "N/A"
        lines.append(f"  {q or 'N/A':<10}  {act_s:>8}  {est_s:>10}  {dif_s:>8}")
    return "\n".join(lines)


def _revenue_section(detail: StockDetail) -> str:
    if not detail.rev_quarters:
        return ""
    lines = [
        _section("Revenue & Net Income ($B)"),
        f"  {'Quarter':<10}  {'Revenue':>10}  {'Net Income':>12}",
        "  " + "-" * 36,
    ]
    for q, rev, earn in zip_longest(
        detail.rev_quarters, detail.rev_values, detail.earn_values
    ):
        rev_s = f"{rev:.2f}" if rev is not None else "N/A"
        earn_s = f"{earn:.2f}" if earn is not None else "N/A"
        lines.append(f"  {q or 'N/A':<10}  {rev_s:>10}  {earn_s:>12}")
    return "\n".join(lines)


def _analyst_section(detail: StockDetail) -> str:
    rows: list[tuple[str, str]] = [
        ("Recommendation", detail.recommendation_key.title() or "N/A"),
        ("Analysts", str(detail.num_analysts)),
    ]
    for k in ("low", "mean", "high"):
        if k in detail.price_targets:
            rows.append((f"Target {k.title()}", f"{detail.price_targets[k]:.2f}"))
    return _section("Analyst Ratings") + "\n" + _kv_table(rows)


def format_detail(detail: StockDetail) -> str:
    """Build a plain-text detail report for a single instrument."""
    parts = [
        f"{detail.symbol} -- {detail.name}",
        _section("Summary"),
        _kv_table(list(detail.summary.items())),
        _performance_section(detail),
        _eps_section(detail),
        _revenue_section(detail),
        _analyst_section(detail),
    ]
    if detail.valuation:
        parts += [_section("Valuation"), _kv_table(list(detail.valuation.items()))]
    if detail.financials:
        parts += [_section("Financials"), _kv_table(list(detail.financials.items()))]
    return "\n".join(p for p in parts if p)
