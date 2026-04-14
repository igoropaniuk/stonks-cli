"""Health checks for the stonks-cli environment."""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
import httpx

from stonks_cli import __version__
from stonks_cli.models import Portfolio, Position, WatchlistItem, collect_all_items
from stonks_cli.storage import PortfolioStore

try:
    import yfinance as yf
except ImportError:
    yf = None  # type: ignore[assignment]

_MIN_PYTHON = (3, 11)

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

_OK = click.style("OK", fg="green", bold=True)
_WARN = click.style("WARN", fg="yellow", bold=True)
_FAIL = click.style("FAIL", fg="red", bold=True)


def _ok(label: str, detail: str = "") -> None:
    msg = f"  [{_OK}]  {label}"
    if detail:
        msg += f"  ({detail})"
    click.echo(msg)


def _warn(label: str, detail: str = "") -> None:
    msg = f"  [{_WARN}] {label}"
    if detail:
        msg += f"  ({detail})"
    click.echo(msg)


def _fail(label: str, detail: str = "") -> None:
    msg = f"  [{_FAIL}] {label}"
    if detail:
        msg += f"  ({detail})"
    click.echo(msg)


# ---------------------------------------------------------------------------
# yfinance helpers
# ---------------------------------------------------------------------------


def _yf_last_price(fast_info: object) -> float:
    """Return the best available price from a yfinance Ticker.fast_info object.

    Prefers ``last_price``; falls back to ``regular_market_previous_close``
    when ``last_price`` is ``None``; returns ``0.0`` when both are unavailable.
    """
    lp = getattr(fast_info, "last_price", None)
    pc = getattr(fast_info, "regular_market_previous_close", None)
    return float(lp if lp is not None else (pc if pc is not None else 0))


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _load_portfolio(path: Path) -> Portfolio | None:
    """Load and return the portfolio, or None on failure."""
    store = PortfolioStore(path=path)
    try:
        return store.load()
    except ValueError as exc:
        _fail("Parse error", str(exc))
        return None


def check_portfolio(path: Path) -> Portfolio | None:
    """Verify that the portfolio file exists and can be parsed.

    Returns the loaded Portfolio on success, or None if the file is missing
    or unparseable.  A missing file is not treated as a hard failure (first-
    run scenario), but an unparseable file is.
    """
    click.echo(f"\nPortfolio  ({path})")
    if not path.exists():
        _warn("File not found", "run 'stonks demo' or create a portfolio first")
        return Portfolio()  # empty -- downstream checks can still run

    portfolio = _load_portfolio(path)
    if portfolio is None:
        return None

    n_pos = len(portfolio.positions)
    n_cash = len(portfolio.cash)
    n_watch = len(portfolio.watchlist)
    _ok(
        "File parsed successfully",
        f"{n_pos} position(s), {n_cash} cash entry(ies), {n_watch} watchlist item(s)",
    )
    return portfolio


def check_symbols(items: list[Position | WatchlistItem]) -> bool:
    """Probe each symbol and report any that cannot be fetched."""
    from stonks_cli.crypto_fetcher import CryptoFetcher

    # Collect equity and crypto symbols separately
    equity_symbols: list[str] = []
    crypto_symbols: list[str] = []
    crypto_external_ids: dict[str, str] = {}

    for item in items:
        if item.asset_type == "crypto":
            crypto_symbols.append(item.symbol)
            if item.external_id:
                crypto_external_ids[item.symbol] = item.external_id
        else:
            equity_symbols.append(item.symbol)

    if not equity_symbols and not crypto_symbols:
        return True

    click.echo("\nSymbol validation")
    all_ok = True

    # --- Equity symbols via yfinance fast_info (parallel) ---
    def _probe_equity(sym: str) -> tuple[str, float | None, str | None]:
        """Return (symbol, price, error_msg)."""
        if yf is None:
            return sym, None, "yfinance not installed"
        try:
            price = _yf_last_price(yf.Ticker(sym).fast_info)
            return sym, price, None
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            return sym, None, str(exc)

    results: dict[str, tuple[float | None, str | None]] = {}
    if equity_symbols:
        with ThreadPoolExecutor(max_workers=min(8, len(equity_symbols))) as pool:
            futures = {pool.submit(_probe_equity, sym): sym for sym in equity_symbols}
            for fut in as_completed(futures):
                sym, price, err = fut.result()
                results[sym] = (price, err)

    for sym in equity_symbols:  # print in original order
        price, err = results[sym]
        if err is not None:
            _fail(sym, err)
            all_ok = False
        elif price and price > 0:
            _ok(sym, f"last price: {price:.2f}")
        else:
            _warn(sym, "price returned as 0 or None -- symbol may be delisted")

    # --- Crypto symbols via CryptoFetcher (handles batch + per-item fallback) ---
    if crypto_symbols:
        fetcher = CryptoFetcher()
        prices, _ = fetcher.fetch_prices_and_changes(
            crypto_symbols, external_ids=crypto_external_ids or None
        )
        for sym in crypto_symbols:
            if sym in prices:
                _ok(sym, f"last price: {prices[sym]:.2f} USD")
            else:
                _warn(
                    sym,
                    "no price returned -- check symbol or add external_id to YAML",
                )
                all_ok = False

    return all_ok


def check_yfinance() -> bool:
    """Verify that yfinance can reach Yahoo Finance."""
    click.echo("\nyfinance")
    if yf is None:
        _fail("yfinance not installed", "run: pip install yfinance")
        return False
    try:
        price = _yf_last_price(yf.Ticker("AAPL").fast_info)
        if price > 0:
            _ok("Reachable", f"AAPL last price: {price:.2f}")
            return True
        _warn("Response received but price is 0 or None")
        return True
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        _fail("Cannot reach Yahoo Finance", str(exc))
        return False


def check_coingecko() -> bool:
    """Verify that the CoinGecko /ping endpoint responds."""
    click.echo("\nCoinGecko")
    base_url = "https://api.coingecko.com/api/v3"
    headers: dict[str, str] = {}
    api_key = os.environ.get("COINGECKO_DEMO_API_KEY")
    if api_key:
        headers["x-cg-demo-api-key"] = api_key

    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=10.0) as client:
            resp = client.get("/ping")
            resp.raise_for_status()
        gecko_says = resp.json().get("gecko_says", "")
        _ok("Reachable", gecko_says or f"HTTP {resp.status_code}")
        if api_key:
            _ok("Demo API key present (COINGECKO_DEMO_API_KEY)")
        else:
            _warn(
                "No API key set",
                "set COINGECKO_DEMO_API_KEY for higher rate limits",
            )
        return True
    except httpx.HTTPStatusError as exc:
        _fail(f"HTTP {exc.response.status_code}", str(exc))
        return False
    except (httpx.RequestError, OSError) as exc:
        _fail("Cannot reach CoinGecko", str(exc))
        return False


def check_python_version() -> bool:
    """Warn if the running Python is below the minimum supported version."""
    click.echo("\nPython")
    major, minor = sys.version_info[:2]
    version_str = f"{major}.{minor}.{sys.version_info.micro}"
    min_str = ".".join(str(x) for x in _MIN_PYTHON)
    if (major, minor) >= _MIN_PYTHON:
        _ok(f"Python {version_str}", f">= {min_str} required")
        return True
    _fail(f"Python {version_str}", f"minimum required is {min_str}")
    return False


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def check_version() -> bool:
    """Compare the installed version against the latest release on PyPI."""
    click.echo("\nstonks-cli version")
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get("https://pypi.org/pypi/stonks-cli/json")
            resp.raise_for_status()
        latest = resp.json()["info"]["version"]
        if _version_tuple(latest) > _version_tuple(__version__):
            _warn(
                f"Installed: {__version__}",
                f"latest on PyPI is {latest} -- consider upgrading",
            )
        else:
            _ok(f"Up to date ({__version__})", f"latest: {latest}")
        return True
    except (httpx.RequestError, OSError) as exc:
        _warn(f"Installed: {__version__}", f"could not reach PyPI: {exc}")
        return True  # network failure is advisory only
    except (httpx.HTTPStatusError, KeyError, ValueError) as exc:
        _warn(f"Installed: {__version__}", f"PyPI check failed: {exc}")
        return True


def check_forex() -> bool:
    """Fetch a live EUR/USD forex rate via yfinance to validate the forex path."""
    click.echo("\nForex rates")
    if yf is None:
        _fail("yfinance not installed", "run: pip install yfinance")
        return False
    try:
        rate = _yf_last_price(yf.Ticker("EURUSD=X").fast_info)
        if rate > 0:
            _ok("EUR/USD reachable", f"rate: {rate:.4f}")
            return True
        _warn("EUR/USD rate returned 0 or None")
        return True
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        _fail("Cannot fetch forex rate", str(exc))
        return False


def check_exchange_calendars() -> bool:
    """Verify exchange-calendars can open the NYSE calendar and read today's session."""
    click.echo("\nexchange-calendars")
    try:
        import exchange_calendars as xcals  # type: ignore[import-untyped]
        import pandas as pd  # type: ignore[import-untyped]

        cal = xcals.get_calendar("XNYS")
        today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
        is_session = cal.is_session(today)
        status = "trading day" if is_session else "non-trading day (weekend/holiday)"
        _ok("NYSE calendar loaded", f"today is a {status}")
        return True
    except Exception as exc:  # noqa: BLE001
        _fail("exchange-calendars error", str(exc))
        return False


def check_openai() -> None:
    """Report whether the OpenAI key is configured (AI chat feature)."""
    click.echo("\nAI Chat (optional)")
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        masked = key[:8] + "..." if len(key) > 8 else "***"
        _ok("OPENAI_API_KEY is set", masked)
    else:
        _warn("OPENAI_API_KEY not set", "AI chat screen will be disabled")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_doctor(portfolio_paths: list[Path]) -> int:
    """Run all health checks and return the number of failures."""
    click.echo(f"stonks-cli doctor  (Python {sys.version.split()[0]})")
    click.echo("=" * 48)

    failures = 0

    if not check_python_version():
        failures += 1

    check_version()  # advisory: network failure is not a hard error

    if not check_exchange_calendars():
        failures += 1

    # Check each portfolio file and collect loaded portfolios for symbol probing
    loaded_portfolios: list[Portfolio] = []
    for path in portfolio_paths:
        portfolio = check_portfolio(path)
        if portfolio is None:
            failures += 1
        else:
            loaded_portfolios.append(portfolio)

    # Probe API connectivity
    yfinance_ok = check_yfinance()
    coingecko_ok = check_coingecko()
    if not yfinance_ok:
        failures += 1
    if not coingecko_ok:
        failures += 1

    if not check_forex():
        failures += 1

    # Validate individual symbols, skipping items whose API is unreachable
    all_items = collect_all_items(loaded_portfolios)
    if all_items:
        items_to_check = [
            item
            for item in all_items
            if (item.asset_type == "crypto" and coingecko_ok)
            or (item.asset_type != "crypto" and yfinance_ok)
        ]
        if items_to_check and not check_symbols(items_to_check):
            failures += 1

    check_openai()  # advisory only -- never counts as a failure

    click.echo("\n" + "=" * 48)
    if failures == 0:
        click.echo(click.style("All checks passed.", fg="green", bold=True))
    else:
        click.echo(click.style(f"{failures} check(s) failed.", fg="red", bold=True))
    return failures
