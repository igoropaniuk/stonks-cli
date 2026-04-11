"""Portfolio backtesting engine.

Fetches historical price data via yfinance, simulates portfolio growth
against a benchmark, and computes summary statistics.
"""

import logging
from dataclasses import dataclass, field

import yfinance as yf

from stonks_cli.dto import BacktestConfig
from stonks_cli.models import Portfolio

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Holds all data produced by a single backtest run."""

    # Time series (aligned dates)
    dates: list[str] = field(default_factory=list)
    portfolio_values: list[float] = field(default_factory=list)
    benchmark_values: list[float] = field(default_factory=list)

    # Annual returns (year label -> pct)
    annual_years: list[str] = field(default_factory=list)
    annual_portfolio_returns: list[float] = field(default_factory=list)
    annual_benchmark_returns: list[float] = field(default_factory=list)

    # Summary stats
    portfolio_cagr: float = 0.0
    benchmark_cagr: float = 0.0
    portfolio_max_drawdown: float = 0.0
    benchmark_max_drawdown: float = 0.0
    portfolio_sharpe: float = 0.0
    benchmark_sharpe: float = 0.0
    portfolio_best_year: str = ""
    portfolio_worst_year: str = ""
    benchmark_best_year: str = ""
    benchmark_worst_year: str = ""
    portfolio_final: float = 0.0
    benchmark_final: float = 0.0
    total_contributions: float = 0.0
    skipped_symbols: list[str] = field(default_factory=list)


def _max_drawdown(values: list[float]) -> float:
    """Return maximum drawdown as a negative percentage."""
    if len(values) < 2:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for v in values[1:]:
        if v > peak:
            peak = v
        dd = (v - peak) / peak * 100 if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _cagr(start_val: float, end_val: float, years: float) -> float:
    """Return CAGR as a percentage."""
    if start_val <= 0 or end_val <= 0 or years <= 0:
        return 0.0
    return ((end_val / start_val) ** (1 / years) - 1) * 100


def _sharpe(annual_returns: list[float], risk_free: float = 2.0) -> float:
    """Return annualised Sharpe ratio (assumes annual return data)."""
    if len(annual_returns) < 2:
        return 0.0
    excess = [r - risk_free for r in annual_returns]
    mean = sum(excess) / len(excess)
    var = sum((x - mean) ** 2 for x in excess) / (len(excess) - 1)
    std = var**0.5
    if std == 0:
        return 0.0
    return mean / std


def _best_worst_year(years: list[str], returns: list[float]) -> tuple[str, str]:
    """Return (best_year_label, worst_year_label) strings."""
    if not returns:
        return ("N/A", "N/A")
    best_idx = max(range(len(returns)), key=lambda i: returns[i])
    worst_idx = min(range(len(returns)), key=lambda i: returns[i])
    return (
        f"{years[best_idx]} ({returns[best_idx]:+.1f}%)",
        f"{years[worst_idx]} ({returns[worst_idx]:+.1f}%)",
    )


def _compute_weights(portfolio: Portfolio) -> dict[str, float]:
    """Return symbol -> fractional weight based on current cost basis."""
    total_cost = sum(p.quantity * p.avg_cost for p in portfolio.positions)
    return {
        p.symbol: (p.quantity * p.avg_cost) / total_cost if total_cost else 0
        for p in portfolio.positions
    }


def run_backtest(portfolio: Portfolio, config: BacktestConfig) -> BacktestResult:
    """Run a portfolio backtest and return the result.

    The portfolio's current allocation weights are used throughout the
    backtest period.  Historical prices are fetched from yfinance.
    """
    symbols = [p.symbol for p in portfolio.positions]
    if not symbols:
        raise ValueError("Portfolio has no equity positions to backtest")

    weights = _compute_weights(portfolio)

    benchmark = config["benchmark"]
    start_year = config["start_year"]
    end_year = config["end_year"]
    start_amount = config["start_amount"]
    cashflows = config["cashflows"]
    rebalance = config["rebalance"]
    skip_unavailable = config["skip_unavailable"]

    start_date = f"{start_year}-01-01"
    end_date = f"{end_year}-12-31"

    # Fetch historical data
    all_symbols = list(dict.fromkeys(symbols + [benchmark]))
    logger.info("Fetching history for %s (%s to %s)", all_symbols, start_date, end_date)
    data = yf.download(all_symbols, start=start_date, end=end_date, progress=False)

    if data.empty:
        raise ValueError("No historical data available for the given date range")

    # Extract adjusted close prices
    if "Close" in data.columns or hasattr(data.columns, "levels"):
        if len(all_symbols) > 1:
            close = data["Close"]
        else:
            close = data[["Close"]].rename(columns={"Close": symbols[0]})
    else:
        raise ValueError("Unexpected data format from yfinance")

    # For single-symbol portfolio + benchmark, ensure columns exist
    if len(all_symbols) == 1:
        close.columns = all_symbols

    # Validate that all symbols have data covering the start year
    missing: list[str] = []
    unavailable_syms: set[str] = set()
    for sym in all_symbols:
        if sym not in close.columns or close[sym].isna().all():
            unavailable_syms.add(sym)
            missing.append(sym)
            continue
        first_valid = close[sym].first_valid_index()
        if first_valid is not None and first_valid.year > start_year:
            unavailable_syms.add(sym)
            missing.append(f"{sym} (available from {first_valid.year})")

    if missing:
        # Benchmark must always be available
        if benchmark in unavailable_syms:
            raise ValueError("Quotes are not available for: " + ", ".join(missing))
        if not skip_unavailable:
            raise ValueError("Quotes are not available for: " + ", ".join(missing))
        # Remove unavailable symbols and redistribute weights
        logger.info("Skipping unavailable symbols: %s", unavailable_syms)
        symbols = [s for s in symbols if s not in unavailable_syms]
        for sym in unavailable_syms:
            weights.pop(sym, None)
        if not symbols:
            raise ValueError(
                "No positions with available data after skipping: " + ", ".join(missing)
            )
        # Redistribute weights to sum to 1.0
        total_w = sum(weights.values())
        if total_w <= 0:
            raise ValueError(
                "No positions with a valid cost basis remain; "
                "cannot determine weights for backtesting."
            )
        weights = {s: w / total_w for s, w in weights.items()}

    # Drop rows where benchmark has no data
    if benchmark in close.columns:
        close = close.dropna(subset=[benchmark])
    close = close.ffill().bfill()

    if close.empty:
        raise ValueError("No overlapping data for portfolio symbols and benchmark")

    # Simulate portfolio growth
    dates_idx = close.index
    n = len(dates_idx)

    # Portfolio simulation
    portfolio_vals = [0.0] * n
    benchmark_vals = [0.0] * n

    # Initialise portfolio: allocate start_amount across symbols by weight
    shares: dict[str, float] = {}
    for sym in symbols:
        if sym in close.columns and not close[sym].isna().all():
            price = close[sym].iloc[0]
            if price > 0:
                shares[sym] = (start_amount * weights[sym]) / price
            else:
                shares[sym] = 0.0
        else:
            shares[sym] = 0.0

    # Benchmark: buy start_amount worth
    bench_price_0 = close[benchmark].iloc[0] if benchmark in close.columns else 1.0
    bench_shares = start_amount / bench_price_0 if bench_price_0 > 0 else 0.0

    total_contributed = start_amount
    last_cashflow_year = dates_idx[0].year

    for i in range(n):
        dt = dates_idx[i]

        # Annual cashflow injection (at start of each new year)
        if cashflows > 0 and dt.year > last_cashflow_year:
            years_passed = dt.year - last_cashflow_year
            for _ in range(years_passed):
                total_contributed += cashflows
                # Add to portfolio proportionally
                for sym in symbols:
                    if sym in close.columns:
                        price = close[sym].iloc[i]
                        if price > 0:
                            shares[sym] += (cashflows * weights[sym]) / price
                # Add to benchmark
                if benchmark in close.columns:
                    bp = close[benchmark].iloc[i]
                    if bp > 0:
                        bench_shares += cashflows / bp
            last_cashflow_year = dt.year

        # Rebalancing
        if rebalance != "none" and i > 0:
            should_rebalance = False
            if rebalance == "monthly" and dates_idx[i].month != dates_idx[i - 1].month:
                should_rebalance = True
            elif rebalance == "annual" and dates_idx[i].year != dates_idx[i - 1].year:
                should_rebalance = True

            if should_rebalance:
                # Calculate current total value
                current_total = 0.0
                for sym in symbols:
                    if sym in close.columns:
                        current_total += shares[sym] * close[sym].iloc[i]
                # Redistribute
                if current_total > 0:
                    for sym in symbols:
                        if sym in close.columns:
                            price = close[sym].iloc[i]
                            if price > 0:
                                shares[sym] = (current_total * weights[sym]) / price

        # Calculate values
        pv = 0.0
        for sym in symbols:
            if sym in close.columns:
                pv += shares[sym] * close[sym].iloc[i]
        portfolio_vals[i] = pv

        if benchmark in close.columns:
            benchmark_vals[i] = bench_shares * close[benchmark].iloc[i]

    # Format dates
    date_strings = [d.strftime("%Y-%m-%d") for d in dates_idx]

    # Annual returns
    annual_years: list[str] = []
    annual_port_ret: list[float] = []
    annual_bench_ret: list[float] = []

    year_start_port = portfolio_vals[0]
    year_start_bench = benchmark_vals[0]
    current_year = dates_idx[0].year

    for i in range(1, n):
        if dates_idx[i].year != current_year:
            # End of year
            if year_start_port > 0:
                annual_port_ret.append(
                    (portfolio_vals[i - 1] - year_start_port) / year_start_port * 100
                )
            else:
                annual_port_ret.append(0.0)
            if year_start_bench > 0:
                annual_bench_ret.append(
                    (benchmark_vals[i - 1] - year_start_bench) / year_start_bench * 100
                )
            else:
                annual_bench_ret.append(0.0)
            annual_years.append(str(current_year))
            year_start_port = portfolio_vals[i]
            year_start_bench = benchmark_vals[i]
            current_year = dates_idx[i].year

    # Last (partial) year
    if n > 1 and portfolio_vals[-1] != year_start_port:
        if year_start_port > 0:
            annual_port_ret.append(
                (portfolio_vals[-1] - year_start_port) / year_start_port * 100
            )
        else:
            annual_port_ret.append(0.0)
        if year_start_bench > 0:
            annual_bench_ret.append(
                (benchmark_vals[-1] - year_start_bench) / year_start_bench * 100
            )
        else:
            annual_bench_ret.append(0.0)
        annual_years.append(str(current_year))

    # Summary stats
    total_days = (dates_idx[-1] - dates_idx[0]).days
    total_years = total_days / 365.25 if total_days > 0 else 1.0

    result = BacktestResult(
        dates=date_strings,
        portfolio_values=portfolio_vals,
        benchmark_values=benchmark_vals,
        annual_years=annual_years,
        annual_portfolio_returns=annual_port_ret,
        annual_benchmark_returns=annual_bench_ret,
        portfolio_cagr=_cagr(portfolio_vals[0], portfolio_vals[-1], total_years),
        benchmark_cagr=_cagr(benchmark_vals[0], benchmark_vals[-1], total_years),
        portfolio_max_drawdown=_max_drawdown(portfolio_vals),
        benchmark_max_drawdown=_max_drawdown(benchmark_vals),
        portfolio_sharpe=_sharpe(annual_port_ret),
        benchmark_sharpe=_sharpe(annual_bench_ret),
        portfolio_best_year=_best_worst_year(annual_years, annual_port_ret)[0],
        portfolio_worst_year=_best_worst_year(annual_years, annual_port_ret)[1],
        benchmark_best_year=_best_worst_year(annual_years, annual_bench_ret)[0],
        benchmark_worst_year=_best_worst_year(annual_years, annual_bench_ret)[1],
        portfolio_final=portfolio_vals[-1],
        benchmark_final=benchmark_vals[-1],
        total_contributions=total_contributed,
        skipped_symbols=sorted(unavailable_syms - {benchmark}),
    )
    return result
