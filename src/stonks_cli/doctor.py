"""Health checks for the stonks-cli environment."""

import os
import sys
from pathlib import Path

import click
import httpx
import yfinance as yf

from stonks_cli.models import Portfolio, Position, WatchlistItem, collect_all_items
from stonks_cli.storage import PortfolioStore

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


def _resolve_coin_id(symbol: str, external_id: str | None) -> str | None:
    """Return the CoinGecko coin ID for a crypto symbol.

    Priority: explicit external_id > bundled coin map > None.
    """
    if external_id:
        return external_id
    from stonks_cli.crypto_fetcher import CryptoFetcher

    CryptoFetcher._ensure_coin_list()
    from stonks_cli.crypto_fetcher import _cg_symbol_to_id

    base = symbol.upper().split("-")[0]
    return _cg_symbol_to_id.get(base)


def check_symbols(items: list[Position | WatchlistItem]) -> bool:
    """Probe each symbol and report any that cannot be fetched."""
    # Collect equity items and crypto items (with their external_id) separately
    equity_symbols: list[str] = []
    # list of (display_symbol, coin_id_or_None)
    crypto_items: list[tuple[str, str | None]] = []

    for item in items:
        if getattr(item, "asset_type", None) == "crypto":
            coin_id = _resolve_coin_id(item.symbol, getattr(item, "external_id", None))
            crypto_items.append((item.symbol, coin_id))
        else:
            equity_symbols.append(item.symbol)

    if not equity_symbols and not crypto_items:
        return True

    click.echo("\nSymbol validation")
    all_ok = True

    # --- Equity symbols via yfinance fast_info ---
    for sym in equity_symbols:
        try:
            info = yf.Ticker(sym).fast_info
            price = float(info.last_price or info.regular_market_previous_close or 0)
            if price > 0:
                _ok(sym, f"last price: {price:.2f}")
            else:
                _warn(sym, "price returned as 0 or None -- symbol may be delisted")
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            _fail(sym, str(exc))
            all_ok = False

    # --- Crypto symbols via CoinGecko /simple/price (batch by coin ID) ---
    if crypto_items:
        api_key = os.environ.get("COINGECKO_DEMO_API_KEY")
        headers: dict[str, str] = {}
        if api_key:
            headers["x-cg-demo-api-key"] = api_key

        # Separate items we could resolve from those we could not
        resolvable = [(sym, cid) for sym, cid in crypto_items if cid]
        unresolvable = [sym for sym, cid in crypto_items if not cid]

        for sym in unresolvable:
            _warn(sym, "unknown coin ID -- add external_id to YAML for price lookup")

        if resolvable:
            # coin_id -> symbol (for result reporting)
            id_to_sym = {cid: sym for sym, cid in resolvable}
            ids_param = ",".join(id_to_sym.keys())
            try:
                with httpx.Client(
                    base_url="https://api.coingecko.com/api/v3",
                    headers=headers,
                    timeout=15.0,
                ) as client:
                    resp = client.get(
                        "/simple/price",
                        params={"ids": ids_param, "vs_currencies": "usd"},
                    )
                    resp.raise_for_status()
                data: dict = resp.json()
                for coin_id, sym in id_to_sym.items():
                    if coin_id in data and "usd" in data[coin_id]:
                        price = data[coin_id]["usd"]
                        _ok(sym, f"last price: {price:.2f} USD (id: {coin_id})")
                    else:
                        _warn(sym, f"coin ID '{coin_id}' returned no price")
            except (httpx.HTTPStatusError, httpx.RequestError, OSError) as exc:
                for sym, _ in resolvable:
                    _fail(sym, f"CoinGecko error: {exc}")
                all_ok = False

    return all_ok


def check_yfinance() -> bool:
    """Verify that yfinance can reach Yahoo Finance."""
    click.echo("\nyfinance")
    try:
        ticker = yf.Ticker("AAPL")
        info = ticker.fast_info
        price = float(info.last_price or info.regular_market_previous_close or 0)
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

    # Validate individual symbols only when APIs are reachable
    all_items = collect_all_items(loaded_portfolios)
    if all_items:
        if not check_symbols(all_items):
            failures += 1

    check_openai()  # advisory only -- never counts as a failure

    click.echo("\n" + "=" * 48)
    if failures == 0:
        click.echo(click.style("All checks passed.", fg="green", bold=True))
    else:
        click.echo(click.style(f"{failures} check(s) failed.", fg="red", bold=True))
    return failures
