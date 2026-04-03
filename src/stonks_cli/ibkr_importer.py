"""Interactive Brokers Flex Query CSV importer.

Supports two CSV formats:

1. **IBKR Flex Query multi-section format** -- the standard output when you
   run a Flex Query and export as CSV.  Each row starts with the section
   name (e.g. ``"Open Positions"``) followed by the row type
   (``"Header"``, ``"Data"``, ``"Total"``).

2. **Plain CSV** -- a simple file where the first row contains column
   headers and subsequent rows contain data.  Useful when the IBKR export
   is post-processed or when a different broker uses compatible field names.

Recognised column names (case-insensitive):

    Symbol          -- ticker symbol  (required)
    Position        -- share quantity (required)
    OpenPrice / Average Price / Avg Price / CostBasisPrice -- avg cost (required)
    CurrencyPrimary / Currency   -- currency (optional, defaults to USD)
    AssetClass / Asset Class     -- filters non-equity rows (optional)
    ListingExchange              -- IBKR exchange code used to append the
                                   correct yfinance suffix to the symbol
                                   (optional; symbols without a suffix stay
                                   as-is, i.e. treated as US tickers)
"""

import csv
import io
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Column name aliases (matched case-insensitively)
# ---------------------------------------------------------------------------

_SYMBOL_ALIASES: frozenset[str] = frozenset({"symbol"})
_QUANTITY_ALIASES: frozenset[str] = frozenset({"position", "quantity"})
_AVG_PRICE_ALIASES: frozenset[str] = frozenset(
    {"openprice", "open price", "average price", "avg price", "costbasisprice"}
)
_CURRENCY_ALIASES: frozenset[str] = frozenset({"currencyprimary", "currency"})
_ASSET_CLASS_ALIASES: frozenset[str] = frozenset({"assetclass", "asset class"})
_EXCHANGE_ALIASES: frozenset[str] = frozenset({"listingexchange", "listing exchange"})

# Asset classes treated as equities (all others are skipped)
_EQUITY_ASSET_CLASSES: frozenset[str] = frozenset({"STK", "STOCK"})

# IBKR Flex Query section name for open positions
_FLEX_SECTION = "Open Positions"

# ---------------------------------------------------------------------------
# IBKR ListingExchange -> yfinance ticker suffix
#
# US venues produce no suffix (None).  Non-US venues map to the dot-suffix
# used by yfinance (e.g. "L" -> symbol becomes "BP.L").
# IBKR sometimes appends ".ETF" to the exchange code for ETFs traded on the
# same venue -- these are normalised by stripping the ".ETF" part first.
# ---------------------------------------------------------------------------

_IBKR_EXCHANGE_TO_SUFFIX: dict[str, str | None] = {
    # United States -- no suffix
    "NASDAQ": None,
    "NYSE": None,
    "ARCA": None,
    "BATS": None,
    "ISLAND": None,
    "IEX": None,
    # Europe
    "LSE": "L",  # London Stock Exchange
    "LSEETF": "L",  # LSE ETF segment
    "SBF": "PA",  # Euronext Paris (Société de Bourse Française)
    "AEB": "AS",  # Euronext Amsterdam
    "ENEXT.BE": "BR",  # Euronext Brussels
    "IBIS": "DE",  # XETRA
    "IBIS2": "DE",  # XETRA (alternative segment)
    "BVME": "MI",  # Borsa Italiana (Milan)
    "VSE": "VI",  # Vienna Stock Exchange
    "SWX": "SW",  # SIX Swiss Exchange
    "VIRTX": "SW",  # SIX (formerly virt-x)
    "OSE": "OL",  # Oslo Bors
    "STO": "ST",  # Nasdaq Stockholm
    "CPH": "CO",  # Nasdaq Copenhagen
    "HEX": "HE",  # Nasdaq Helsinki
    "WSE": "WA",  # Warsaw Stock Exchange
    "ATHEX": "AT",  # Athens Stock Exchange
    "LISBON": "LS",  # Euronext Lisbon
    # Americas (non-US)
    "TSX": "TO",  # Toronto Stock Exchange
    "TSXV": "V",  # TSX Venture
    "MEXI": "MX",  # Bolsa Mexicana de Valores
    "BVMF": "SA",  # B3 São Paulo
    # Asia-Pacific
    "TSE": "T",  # Tokyo Stock Exchange
    "OSE.JPN": "T",  # Osaka (merged into TSE)
    "SEHK": "HK",  # Hong Kong Stock Exchange
    "ASX": "AX",  # Australian Securities Exchange
    "NZX": "NZ",  # New Zealand Exchange
    "SGX": "SI",  # Singapore Exchange
    "KSE": "KS",  # Korea Stock Exchange
    "KOSDAQ": "KQ",  # KOSDAQ
    "TWSE": "TW",  # Taiwan Stock Exchange
    "OTC.BB": None,  # OTC Bulletin Board (US)
}


def _exchange_suffix(ibkr_code: str) -> str | None:
    """Return the yfinance dot-suffix for an IBKR ``ListingExchange`` code.

    Strips a trailing ``.ETF`` segment before the lookup so that e.g.
    ``"BVME.ETF"`` resolves the same as ``"BVME"``.

    Returns ``None`` for US exchanges (no suffix needed) and for unknown
    codes (symbol is left unchanged).
    """
    normalised = ibkr_code.upper()
    if normalised.endswith(".ETF"):
        normalised = normalised[:-4]
    return _IBKR_EXCHANGE_TO_SUFFIX.get(normalised)  # None = US or unknown


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class IBKRPosition:
    """A single position parsed from an IBKR CSV export.

    Attributes:
        symbol:      Ticker symbol (uppercased).
        quantity:    Number of shares / units held (positive).
        avg_price:   Average cost per share / unit.
        currency:    Currency of the position (ISO 4217, uppercased).
        asset_class: Raw asset class string from IBKR (e.g. ``"STK"``), or
                     ``None`` when the column is absent.
    """

    symbol: str
    quantity: float
    avg_price: float
    currency: str
    asset_class: str | None


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class IBKRImportError(ValueError):
    """Raised when the CSV file cannot be parsed as an IBKR export."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_col(headers: list[str], aliases: frozenset[str]) -> str | None:
    """Return the first header whose lowercased, stripped form is in *aliases*."""
    for h in headers:
        if h.strip().lower() in aliases:
            return h
    return None


def _parse_float(raw: str, field: str) -> float:
    """Parse a numeric string, accepting commas as thousands separators.

    Raises:
        IBKRImportError: If *raw* cannot be converted to float.
    """
    try:
        return float(raw.replace(",", "").strip())
    except (ValueError, AttributeError) as exc:
        raise IBKRImportError(
            f"Cannot parse numeric value for {field!r}: {raw!r}"
        ) from exc


def _detect_format(rows: list[list[str]]) -> str:
    """Detect CSV format: ``"flex"`` for IBKR Flex Query, ``"simple"`` otherwise."""
    for row in rows[:10]:
        if len(row) >= 2 and row[1].strip() in ("Header", "Data", "Total", "SubTotal"):
            return "flex"
    return "simple"


def _extract_flex(
    rows: list[list[str]],
) -> tuple[list[str], list[list[str]]]:
    """Pull headers and data rows out of an IBKR Flex Query multi-section CSV.

    Raises:
        IBKRImportError: If the ``"Open Positions"`` section is not found.
    """
    headers: list[str] = []
    data_rows: list[list[str]] = []

    for row in rows:
        if len(row) < 2:
            continue
        section = row[0].strip()
        row_type = row[1].strip()
        if section != _FLEX_SECTION:
            continue
        if row_type == "Header":
            headers = [h.strip() for h in row[2:]]
        elif row_type == "Data":
            data_rows.append([v.strip() for v in row[2:]])

    if not headers:
        raise IBKRImportError(
            "Could not find an 'Open Positions' header row "
            "in the IBKR Flex Query CSV. Make sure you included "
            "the 'Open Positions' section when creating the query."
        )

    return headers, data_rows


def _extract_simple(
    rows: list[list[str]],
) -> tuple[list[str], list[list[str]]]:
    """Pull headers and data rows from a plain CSV file.

    Raises:
        IBKRImportError: If the file is empty.
    """
    if not rows:
        raise IBKRImportError("The CSV file is empty.")
    headers = [h.strip() for h in rows[0]]
    data_rows = [
        [v.strip() for v in row] for row in rows[1:] if any(v.strip() for v in row)
    ]
    return headers, data_rows


# ---------------------------------------------------------------------------
# Column index resolution
# ---------------------------------------------------------------------------


@dataclass
class _Columns:
    sym: int
    qty: int
    price: int
    price_name: str
    currency: int | None
    asset_class: int | None
    exchange: int | None


def _resolve_columns(headers: list[str]) -> _Columns:
    """Map header names to integer indices.

    Raises:
        IBKRImportError: If any required column is absent.
    """
    symbol_col = _find_col(headers, _SYMBOL_ALIASES)
    quantity_col = _find_col(headers, _QUANTITY_ALIASES)
    price_col = _find_col(headers, _AVG_PRICE_ALIASES)
    currency_col = _find_col(headers, _CURRENCY_ALIASES)
    asset_class_col = _find_col(headers, _ASSET_CLASS_ALIASES)
    exchange_col = _find_col(headers, _EXCHANGE_ALIASES)

    missing: list[str] = []
    if symbol_col is None:
        missing.append("Symbol")
    if quantity_col is None:
        missing.append("Position / Quantity")
    if price_col is None:
        missing.append("OpenPrice / CostBasisPrice / Average Price")
    if missing:
        raise IBKRImportError(
            f"Missing required column(s): {', '.join(missing)}.\n"
            f"Columns found in file: {', '.join(headers)}"
        )

    # After the None checks above, these three are guaranteed non-None.
    assert symbol_col is not None
    assert quantity_col is not None
    assert price_col is not None

    return _Columns(
        sym=headers.index(symbol_col),
        qty=headers.index(quantity_col),
        price=headers.index(price_col),
        price_name=price_col,
        currency=headers.index(currency_col) if currency_col is not None else None,
        asset_class=headers.index(asset_class_col)
        if asset_class_col is not None
        else None,
        exchange=headers.index(exchange_col) if exchange_col is not None else None,
    )


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------


def _parse_row(row: list[str], line_no: int, cols: _Columns) -> IBKRPosition | None:
    """Parse one data row into an :class:`IBKRPosition`, or return ``None`` to skip."""
    if len(row) <= max(cols.sym, cols.qty, cols.price):
        return None  # truncated row

    symbol = row[cols.sym]
    if not symbol:
        return None

    # Asset-class filter
    asset_class: str | None = None
    if cols.asset_class is not None and len(row) > cols.asset_class:
        asset_class = row[cols.asset_class].upper() or None
    if asset_class is not None and asset_class not in _EQUITY_ASSET_CLASSES:
        return None

    try:
        quantity = _parse_float(row[cols.qty], "Position")
    except IBKRImportError as exc:
        raise IBKRImportError(f"Row {line_no}: {exc}") from exc
    if quantity <= 0:
        return None  # short or zero position

    try:
        avg_price = _parse_float(row[cols.price], cols.price_name)
    except IBKRImportError as exc:
        raise IBKRImportError(f"Row {line_no}: {exc}") from exc
    if avg_price <= 0:
        raise IBKRImportError(
            f"Row {line_no}: average price must be positive for {symbol!r}, "
            f"got {avg_price!r}."
        )

    currency = "USD"
    if cols.currency is not None and len(row) > cols.currency:
        currency = row[cols.currency].upper() or "USD"

    # Apply yfinance suffix based on ListingExchange
    raw_symbol = symbol.upper()
    if cols.exchange is not None and len(row) > cols.exchange:
        ibkr_exchange = row[cols.exchange].strip()
        suffix = _exchange_suffix(ibkr_exchange) if ibkr_exchange else None
        yf_symbol = f"{raw_symbol}.{suffix}" if suffix else raw_symbol
    else:
        yf_symbol = raw_symbol

    return IBKRPosition(
        symbol=yf_symbol,
        quantity=quantity,
        avg_price=avg_price,
        currency=currency,
        asset_class=asset_class,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_ibkr_csv(path: Path) -> list[IBKRPosition]:
    """Parse an IBKR Flex Query (or compatible) CSV and return equity positions.

    Only rows with a positive quantity are returned.  When the ``AssetClass``
    column is present, non-equity rows (options, futures, forex, etc.) are
    skipped automatically.

    Args:
        path: Path to the CSV file.

    Returns:
        List of :class:`IBKRPosition` objects, one per valid equity row.

    Raises:
        IBKRImportError: If the file cannot be read or required columns are missing.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")  # strip BOM if present
    except OSError as exc:
        raise IBKRImportError(f"Cannot read file '{path}': {exc}") from exc

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise IBKRImportError(f"File is empty: {path}")

    if _detect_format(rows) == "flex":
        headers, data_rows = _extract_flex(rows)
    else:
        headers, data_rows = _extract_simple(rows)

    cols = _resolve_columns(headers)

    return [
        pos
        for line_no, row in enumerate(data_rows, start=2)
        if (pos := _parse_row(row, line_no, cols)) is not None
    ]
