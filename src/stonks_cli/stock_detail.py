"""Stock detail data model, format helpers, and fetcher."""

import logging
import math
import zoneinfo
from dataclasses import dataclass
from datetime import datetime

import yfinance as yf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _finite(value) -> float | None:
    """Convert *value* to float, returning None for None/NaN/inf."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _period_to_month(period: str) -> str:
    """Convert '0m', '-1m', '-2m' etc. to 'Mar 2026' style labels."""
    try:
        offset = int(period.replace("m", ""))
    except (ValueError, AttributeError) as exc:
        logger.debug("Cannot parse period %r: %s", period, exc)
        return period
    today = datetime.now(tz=zoneinfo.ZoneInfo("UTC"))
    # Shift month by offset, handling year boundaries
    month = today.month + offset
    year = today.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return datetime(year, month, 1).strftime("%b %Y")


def _fmt_price(v) -> str:
    f = _finite(v)
    return f"{f:.2f}" if f is not None else "N/A"


def _fmt_bid_ask(price, size) -> str:
    p = _finite(price)
    s = _finite(size)
    if p is None:
        return "N/A"
    size_str = f" x {int(s)}" if s is not None else ""
    return f"{p:.2f}{size_str}"


def _fmt_range(low, high) -> str:
    lo = _finite(low)
    hi = _finite(high)
    if lo is None or hi is None:
        return "N/A"
    return f"{lo:.2f} - {hi:.2f}"


def _fmt_int(v) -> str:
    f = _finite(v)
    return f"{int(f):,}" if f is not None else "N/A"


def _fmt_large(v) -> str:
    f = _finite(v)
    if f is None:
        return "N/A"
    af = abs(f)
    if af >= 1e12:
        return f"{f / 1e12:.2f}T"
    if af >= 1e9:
        return f"{f / 1e9:.2f}B"
    if af >= 1e6:
        return f"{f / 1e6:.2f}M"
    return f"{f:,.0f}"


def _fmt_dec(v, places: int = 2) -> str:
    f = _finite(v)
    return f"{f:.{places}f}" if f is not None else "N/A"


def _fmt_pct(v) -> str:
    f = _finite(v)
    return f"{f * 100:.2f}%" if f is not None else "N/A"


def _fiscal_quarter(ts) -> str:
    """Convert a pandas Timestamp to a fiscal quarter label like 'Q1 FY25'."""
    month = ts.month
    year = ts.year
    q = (month - 1) // 3 + 1
    return f"Q{q} FY{year % 100}"


def _trailing_return(hist) -> str:
    """Calculate trailing total return from a price history DataFrame."""
    if hist is None or hist.empty or len(hist) < 2:
        return "N/A"
    try:
        first = float(hist["Close"].iloc[0])
        last = float(hist["Close"].iloc[-1])
        if first == 0:
            return "N/A"
        ret = (last - first) / first * 100
        sign = "+ " if ret >= 0 else "- "
        return f"{sign}{abs(ret):.2f}%"
    except (KeyError, IndexError, TypeError):
        return "N/A"


def _calc_performance(
    symbol: str,
) -> dict[str, tuple[str, str]]:
    """Compute trailing returns for *symbol* vs S&P 500 (^GSPC).

    Returns a dict like ``{"YTD Return": ("+8.14%", "+3.08%"), ...}``.
    """
    periods = {
        "YTD Return": "ytd",
        "1-Year Return": "1y",
        "3-Year Return": "3y",
        "5-Year Return": "5y",
    }
    result: dict[str, tuple[str, str]] = {}
    try:
        stock_ticker = yf.Ticker(symbol)
        sp_ticker = yf.Ticker("^GSPC")
        for label, period in periods.items():
            stock_hist = stock_ticker.history(period=period)
            sp_hist = sp_ticker.history(period=period)
            result[label] = (_trailing_return(stock_hist), _trailing_return(sp_hist))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Performance calculation failed for %s: %s", symbol, exc)
    return result


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class StockDetail:
    """Comprehensive detail data for a single stock."""

    symbol: str
    name: str  # full company/fund name
    # Performance overview (trailing returns vs S&P 500)
    performance: dict[str, tuple[str, str]]  # label -> (stock_return, sp500_return)
    # Price history per period: label -> (dates, closes)
    price_histories: dict[str, tuple[list[str], list[float]]]
    # Financial summary (label -> formatted value)
    summary: dict[str, str]
    # Earnings trends (quarterly)
    eps_quarters: list[str]
    eps_actual: list[float | None]
    eps_estimate: list[float | None]
    eps_diff: list[float | None]
    next_earnings_date: str
    next_eps_estimate: float | None
    # Revenue vs Earnings (quarterly)
    rev_quarters: list[str]
    rev_values: list[float]
    earn_values: list[float]
    # Analyst
    price_targets: dict[str, float]
    recommendations: list[dict[str, int | str]]
    recommendation_key: str
    num_analysts: int
    # Statistics
    valuation: dict[str, str]
    financials: dict[str, str]


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


class StockDetailFetcher:
    """Fetches comprehensive detail data for a single stock ticker."""

    @staticmethod
    def _fetch_price_histories(
        t: yf.Ticker,
        symbol: str,
    ) -> dict[str, tuple[list[str], list[float]]]:
        chart_periods = {
            "1 Day": ("1d", "5m"),
            "1 Month": ("1mo", None),
            "1 Year": ("1y", None),
            "5 Years": ("5y", None),
        }
        result: dict[str, tuple[list[str], list[float]]] = {}
        for label, (period, interval) in chart_periods.items():
            try:
                kw: dict[str, str] = {"period": period}
                if interval:
                    kw["interval"] = interval
                h = t.history(**kw)
                if h is not None and not h.empty:
                    fmt = "%H:%M" if interval else "%Y-%m-%d"
                    # Filter out NaN/inf values that would crash plotext
                    h_finite = h[h["Close"].apply(_finite).notna()]
                    if not h_finite.empty:
                        result[label] = (
                            h_finite.index.strftime(fmt).tolist(),
                            h_finite["Close"].astype(float).tolist(),
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Cannot fetch price history for %s (%s): %s", symbol, period, exc
                )
        return result

    @staticmethod
    def _fetch_summary(info: dict) -> dict[str, str]:
        earnings_ts = _finite(info.get("earningsTimestampStart")) or _finite(
            info.get("earningsTimestamp")
        )
        earnings_date = "N/A"
        if earnings_ts is not None:
            earnings_date = datetime.fromtimestamp(int(earnings_ts)).strftime(
                "%b %d, %Y"
            )

        ex_div_ts = _finite(info.get("exDividendDate"))
        ex_div_date = "N/A"
        if ex_div_ts is not None:
            ex_div_date = datetime.fromtimestamp(int(ex_div_ts)).strftime("%b %d, %Y")

        div_rate = _finite(info.get("dividendRate"))
        div_yield = _finite(info.get("dividendYield"))
        if div_rate is not None and div_yield is not None:
            fwd_div = f"{div_rate:.2f} ({div_yield * 100:.2f}%)"
        else:
            fwd_div = "N/A"

        return {
            "Previous Close": _fmt_price(info.get("previousClose")),
            "Open": _fmt_price(info.get("open")),
            "Bid": _fmt_bid_ask(info.get("bid"), info.get("bidSize")),
            "Ask": _fmt_bid_ask(info.get("ask"), info.get("askSize")),
            "Day's Range": _fmt_range(info.get("dayLow"), info.get("dayHigh")),
            "52 Week Range": _fmt_range(
                info.get("fiftyTwoWeekLow"), info.get("fiftyTwoWeekHigh")
            ),
            "Volume": _fmt_int(info.get("volume")),
            "Avg. Volume": _fmt_int(info.get("averageVolume")),
            "Market Cap (intraday)": _fmt_large(info.get("marketCap")),
            "Beta (5Y Monthly)": _fmt_dec(info.get("beta")),
            "PE Ratio (TTM)": _fmt_dec(info.get("trailingPE")),
            "EPS (TTM)": _fmt_price(info.get("trailingEps")),
            "Earnings Date (est.)": earnings_date,
            "Forward Dividend & Yield": fwd_div,
            "Ex-Dividend Date": ex_div_date,
            "1y Target Est": _fmt_price(info.get("targetMeanPrice")),
        }

    @staticmethod
    def _fetch_earnings(
        t: yf.Ticker, symbol: str, earnings_date: str
    ) -> tuple[
        list[str],
        list[float | None],
        list[float | None],
        list[float | None],
        str,
        float | None,
    ]:
        eps_quarters: list[str] = []
        eps_actual: list[float | None] = []
        eps_estimate: list[float | None] = []
        eps_diff: list[float | None] = []
        try:
            eh = t.earnings_history
            if eh is not None and not eh.empty:
                for q_ts in eh.index:
                    eps_quarters.append(_fiscal_quarter(q_ts))
                    eps_actual.append(_finite(eh.loc[q_ts, "epsActual"]))
                    eps_estimate.append(_finite(eh.loc[q_ts, "epsEstimate"]))
                    eps_diff.append(_finite(eh.loc[q_ts, "epsDifference"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot fetch earnings history for %s: %s", symbol, exc)

        next_eps_estimate: float | None = None
        try:
            ee = t.earnings_estimate
            if ee is not None and not ee.empty and "0q" in ee.index:
                next_eps_estimate = _finite(ee.loc["0q", "avg"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot fetch EPS estimate for %s: %s", symbol, exc)

        return (
            eps_quarters,
            eps_actual,
            eps_estimate,
            eps_diff,
            earnings_date,
            next_eps_estimate,
        )

    @staticmethod
    def _fetch_revenue(
        t: yf.Ticker,
        symbol: str,
    ) -> tuple[list[str], list[float], list[float]]:
        rev_quarters: list[str] = []
        rev_values: list[float] = []
        earn_values: list[float] = []
        try:
            qinc = t.quarterly_income_stmt
            if qinc is not None and not qinc.empty:
                cols = list(reversed(qinc.columns))[:5]
                for col in cols:
                    rev_quarters.append(_fiscal_quarter(col))
                    rev = (
                        _finite(qinc.loc["Total Revenue", col])
                        if "Total Revenue" in qinc.index
                        else None
                    )
                    earn = (
                        _finite(qinc.loc["Net Income", col])
                        if "Net Income" in qinc.index
                        else None
                    )
                    rev_values.append((rev or 0.0) / 1e9)
                    earn_values.append((earn or 0.0) / 1e9)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot fetch revenue data for %s: %s", symbol, exc)
        return rev_quarters, rev_values, earn_values

    @staticmethod
    def _fetch_analyst(
        t: yf.Ticker, symbol: str, info: dict
    ) -> tuple[dict[str, float], list[dict[str, int | str]], str, int]:
        price_targets: dict[str, float] = {}
        try:
            apt = t.analyst_price_targets
            if isinstance(apt, dict):
                for k in ("current", "low", "mean", "median", "high"):
                    v = _finite(apt.get(k))
                    if v is not None:
                        price_targets[k] = v
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot fetch analyst targets for %s: %s", symbol, exc)

        recommendations: list[dict[str, int | str]] = []
        try:
            rs = t.recommendations_summary
            if rs is not None and not rs.empty:
                for _, row in rs.iterrows():
                    recommendations.append(
                        {
                            "period": _period_to_month(str(row.get("period", ""))),
                            "strongBuy": int(row.get("strongBuy", 0)),
                            "buy": int(row.get("buy", 0)),
                            "hold": int(row.get("hold", 0)),
                            "sell": int(row.get("sell", 0)),
                            "strongSell": int(row.get("strongSell", 0)),
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot fetch recommendations for %s: %s", symbol, exc)

        recommendation_key = str(info.get("recommendationKey", "N/A"))
        num_analysts = int(_finite(info.get("numberOfAnalystOpinions")) or 0)
        return price_targets, recommendations, recommendation_key, num_analysts

    @staticmethod
    def _fetch_statistics(info: dict) -> tuple[dict[str, str], dict[str, str]]:
        valuation: dict[str, str] = {
            "Market Cap": _fmt_large(info.get("marketCap")),
            "Enterprise Value": _fmt_large(info.get("enterpriseValue")),
            "Trailing P/E": _fmt_dec(info.get("trailingPE")),
            "Forward P/E": _fmt_dec(info.get("forwardPE")),
            "PEG Ratio (5yr expected)": _fmt_dec(info.get("pegRatio")),
            "Price/Sales (ttm)": _fmt_dec(info.get("priceToSalesTrailing12Months")),
            "Price/Book (mrq)": _fmt_dec(info.get("priceToBook")),
            "Enterprise Value/Revenue": _fmt_dec(info.get("enterpriseToRevenue")),
            "Enterprise Value/EBITDA": _fmt_dec(info.get("enterpriseToEbitda")),
        }
        financials: dict[str, str] = {
            "Profit Margin": _fmt_pct(info.get("profitMargins")),
            "Return on Assets (ttm)": _fmt_pct(info.get("returnOnAssets")),
            "Return on Equity (ttm)": _fmt_pct(info.get("returnOnEquity")),
            "Revenue (ttm)": _fmt_large(info.get("totalRevenue")),
            "Net Income Avail to Common (ttm)": _fmt_large(
                info.get("netIncomeToCommon")
            ),
            "Diluted EPS (ttm)": _fmt_price(info.get("trailingEps")),
            "Total Cash (mrq)": _fmt_large(info.get("totalCash")),
            "Total Debt/Equity (mrq)": _fmt_dec(info.get("debtToEquity")),
        }
        return valuation, financials

    def fetch_stock_detail(self, symbol: str) -> StockDetail:
        """Fetch comprehensive detail for a single ticker."""
        t = yf.Ticker(symbol.upper())
        info = t.info if isinstance(t.info, dict) else {}

        performance = _calc_performance(symbol.upper())
        sym = symbol.upper()
        price_histories = self._fetch_price_histories(t, sym)
        summary = self._fetch_summary(info)
        earnings_date = summary.get("Earnings Date (est.)", "N/A")
        (
            eps_quarters,
            eps_actual,
            eps_estimate,
            eps_diff,
            next_earnings_date,
            next_eps_estimate,
        ) = self._fetch_earnings(t, sym, earnings_date)
        rev_quarters, rev_values, earn_values = self._fetch_revenue(t, sym)
        price_targets, recommendations, recommendation_key, num_analysts = (
            self._fetch_analyst(t, sym, info)
        )
        valuation, financials_dict = self._fetch_statistics(info)

        return StockDetail(
            symbol=symbol.upper(),
            name=str(info.get("longName") or info.get("shortName") or symbol.upper()),
            performance=performance,
            price_histories=price_histories,
            summary=summary,
            eps_quarters=eps_quarters,
            eps_actual=eps_actual,
            eps_estimate=eps_estimate,
            eps_diff=eps_diff,
            next_earnings_date=next_earnings_date,
            next_eps_estimate=next_eps_estimate,
            rev_quarters=rev_quarters,
            rev_values=rev_values,
            earn_values=earn_values,
            price_targets=price_targets,
            recommendations=recommendations,
            recommendation_key=recommendation_key,
            num_analysts=num_analysts,
            valuation=valuation,
            financials=financials_dict,
        )
