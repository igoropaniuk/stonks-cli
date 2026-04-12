"""Health checks for the stonks-cli environment."""

import os
import sys
from pathlib import Path

import click
import httpx
import yfinance as yf

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


def check_portfolio(path: Path) -> bool:
    """Verify that the portfolio file exists and can be parsed."""
    click.echo(f"\nPortfolio  ({path})")
    if not path.exists():
        _warn("File not found", "run 'stonks demo' or create a portfolio first")
        return True  # not a hard failure -- first run is expected

    store = PortfolioStore(path=path)
    try:
        portfolio = store.load()
    except ValueError as exc:
        _fail("Parse error", str(exc))
        return False

    n_pos = len(portfolio.positions)
    n_cash = len(portfolio.cash)
    n_watch = len(portfolio.watchlist)
    _ok(
        "File parsed successfully",
        f"{n_pos} position(s), {n_cash} cash entry(ies), {n_watch} watchlist item(s)",
    )
    return True


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


def run_doctor(portfolio_path: Path) -> int:
    """Run all health checks and return the number of failures."""
    click.echo(f"stonks-cli doctor  (Python {sys.version.split()[0]})")
    click.echo("=" * 48)

    failures = 0

    if not check_portfolio(portfolio_path):
        failures += 1
    if not check_yfinance():
        failures += 1
    if not check_coingecko():
        failures += 1
    check_openai()  # advisory only -- never counts as a failure

    click.echo("\n" + "=" * 48)
    if failures == 0:
        click.echo(click.style("All checks passed.", fg="green", bold=True))
    else:
        click.echo(click.style(f"{failures} check(s) failed.", fg="red", bold=True))
    return failures
