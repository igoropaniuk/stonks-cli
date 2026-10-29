# stonks-cli

[![PyPI version](https://img.shields.io/pypi/v/stonks-cli.svg)](https://pypi.org/project/stonks-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/stonks-cli.svg)](https://pypi.org/project/stonks-cli/)
[![Install](https://img.shields.io/badge/install-pip%20install%20stonks--cli-blue)](https://pypi.org/project/stonks-cli/)
[![Downloads](https://img.shields.io/pypi/dm/stonks-cli)](https://pypi.org/project/stonks-cli/)
[![CI](https://github.com/igoropaniuk/stonks-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/igoropaniuk/stonks-cli/actions)
[![Coverage](https://img.shields.io/codecov/c/github/igoropaniuk/stonks-cli)](https://codecov.io/gh/igoropaniuk/stonks-cli)
[![License](https://img.shields.io/github/license/igoropaniuk/stonks-cli)](https://github.com/igoropaniuk/stonks-cli/blob/main/LICENSE)
[![CLI](https://img.shields.io/badge/CLI-Click-green)](https://click.palletsprojects.com/)

Track your investment portfolio directly from the terminal.

![demo](docs/assets/demo.gif)

**Quick start:**

```bash
pip install stonks-cli
stonks demo # create demo portfolio
```

**Features:**

- **Terminal dashboard (TUI)** -- live prices with auto-refresh
- **Extended-hours quotes** -- PRE / AH / CLS session labels for US equities
- **Candlestick chart screen** -- zoomable OHLC chart
- **News feed panel** -- per-symbol headlines
- **Stock detail screen** -- charts, earnings, analyst insights, key statistics
- **Backtesting** -- simulate historical portfolio performance vs. a benchmark
- **Watchlist** -- track symbols without a position; dimmed in the dashboard
- **Cryptocurrency** -- `BTC-USD`-style symbols priced via CoinGecko
- **Bonds** -- Treasuries priced from Treasury data, others at a manual price
- **Multi-portfolio** -- side-by-side YAML files with multiple `-p` flags
- **Multi-currency** -- totals converted to a base currency using forex rates

## Table of Contents

- [Installation](#installation)
- [Portfolio configuration](#portfolio-configuration)
  - [File structure](#file-structure)
  - [Ticker symbols and exchange suffixes](#ticker-symbols-and-exchange-suffixes)
  - [Americas](#americas)
  - [Canada](#canada)
  - [Europe](#europe)
  - [Asia-Pacific](#asia-pacific)
  - [Cryptocurrency](#cryptocurrency)
  - [Bonds](#bonds)
  - [Examples](#examples)
- [Usage](#usage)
  - [Quick snapshot (stdout)](#quick-snapshot-stdout)
  - [Interactive dashboard (TUI)](#interactive-dashboard-tui)
  - [Add a position](#add-a-position)
  - [Remove a position](#remove-a-position)
  - [Manage cash](#manage-cash)
  - [Manage the watchlist](#manage-the-watchlist)
  - [Portfolio settings](#portfolio-settings)
  - [List portfolios](#list-portfolios)
  - [Importing positions](#importing-positions)
  - [Health checks (doctor)](#health-checks-doctor)
- [Dashboard](#dashboard)
  - [Columns](#columns)
  - [Session labels](#session-labels)
  - [Keyboard shortcuts](#keyboard-shortcuts)
- [News feed](#news-feed)
- [Candlestick chart](#candlestick-chart)
  - [Time ranges](#time-ranges)
  - [Keyboard shortcuts](#keyboard-shortcuts-1)
- [Stock detail screen](#stock-detail-screen)
- [Logging](#logging)
- [Running with Docker](#running-with-docker)
  - [Build the image](#build-the-image)
  - [Run the container](#run-the-container)
- [Backtesting](#backtesting)
- [Market analysis](#market-analysis)
- [Contributing](#contributing)
- [License](#license)

---

## Installation

**Requirements:** Python 3.11+

### [Homebrew](https://brew.sh/) (macOS / Linux)

```bash
brew tap igoropaniuk/tap
brew install stonks-cli
```

### [pip](https://pip.pypa.io/)

```bash
pip install stonks-cli
```

### [pipx](https://pipx.pypa.io/) (recommended -- keeps the tool isolated)

```bash
pipx install stonks-cli
```

### [uv](https://docs.astral.sh/uv/)

```bash
uv tool install stonks-cli
```

## Portfolio configuration

stonks-cli stores your portfolio in a YAML file. By default the file is read
from `~/.config/stonks/portfolio.yaml`; you can override this with the
`-p` / `--portfolio` option (see [Usage](#usage)).

> **Try it first:** run `stonks demo` to explore the dashboard with a sample
> portfolio created in the system temporary directory. Your own portfolio at
> `~/.config/stonks/portfolio.yaml` is never touched.

### File structure

```yaml
portfolio:
  name: Personal          # optional label shown in the dashboard
  base_currency: EUR      # currency of the Total line; defaults to USD
  positions:
    - symbol: AAPL
      quantity: 10
      avg_cost: 150.00
      currency: USD

    - symbol: ASML.AS
      quantity: 5
      avg_cost: 680.00
      currency: EUR

    - symbol: 7203.T
      quantity: 100
      avg_cost: 1850.00
      currency: JPY

    - symbol: BTC-USD
      quantity: 0.5
      avg_cost: 60000.00
      currency: USD
      asset_type: crypto
      external_id: bitcoin

  cash:
    - currency: USD
      amount: 4250.00

    - currency: EUR
      amount: 2100.00

  watchlist:
    - symbol: TSLA
    - symbol: NVDA
    - symbol: LINK-USD
      asset_type: crypto
      external_id: chainlink
```

**Portfolio** fields:

| Field           | Type   | Required | Description                                                     |
| --------------- | ------ | -------- | --------------------------------------------------------------- |
| `name`          | string | no       | Label shown above the table and in multi-portfolio output       |
| `base_currency` | string | no       | ISO 4217 code the Total line is converted to; defaults to `USD` |

Both can also be set with `stonks set name` and `stonks set base-currency`.

**Positions** fields:

| Field         | Type          | Required | Description                                                  |
| ------------- | ------------- | -------- | ------------------------------------------------------------ |
| `symbol`      | string        | yes      | Yahoo Finance ticker (case-insensitive); use `BASE-USD` for crypto |
| `quantity`    | int \| float  | yes      | Units held; fractional values supported for crypto           |
| `avg_cost`    | float         | yes      | Average cost per unit (positive)                             |
| `currency`    | string        | no       | ISO 4217 code; defaults to `USD`                             |
| `asset_type`  | string        | no       | Asset class: `equity`, `crypto`, `etf`, `bond`, `commodity`, `forex` |
| `external_id` | string        | no       | Provider-specific ID used for price lookup  |
| `bond`        | mapping       | no       | Bond terms; see [Bonds](#bonds)                              |

**Cash** fields:

| Field      | Type   | Required | Description |
| ---------- | ------ | -------- | --------------------------------- |
| `currency` | string | yes      | ISO 4217 code (e.g. `USD`, `EUR`) |
| `amount`   | float  | yes      | Cash held (positive) |

**Watchlist** fields:

| Field         | Type   | Required | Description                                                       |
| ------------- | ------ | -------- | ----------------------------------------------------------------- |
| `symbol`      | string | yes      | Yahoo Finance ticker (case-insensitive); use `BASE-USD` for crypto |
| `asset_type`  | string | no       | Same values as positions; set to `crypto` for CoinGecko routing   |
| `external_id` | string | no       | CoinGecko coin ID (or other provider ID) for unambiguous lookup   |

Watchlist items are displayed in the dashboard with a dimmed style and only
show the live price and daily change -- they have no quantity, cost, or market
value and are **not included** in the portfolio total. Press Enter on a
watchlist row to open the detail screen.

> The file is created and updated automatically when you use the `add`,
> `remove`, `add-cash`, `remove-cash`, `watchlist`, and `set` commands, so
> you only need to create it manually if you prefer to seed your data by hand.

### Ticker symbols and exchange suffixes

Yahoo Finance uses a dot-suffix convention to identify non-US exchanges.
Append the appropriate suffix to the base ticker symbol.

### Americas

| Suffix   | Exchange                    | Country   |
| -------- | --------------------------- | --------- |
| *(none)* | NYSE / NASDAQ / AMEX        | USA       |
| `.SA`    | B3 (São Paulo)              | Brazil    |
| `.BA`    | Buenos Aires Stock Exchange | Argentina |
| `.MX`    | Bolsa Mexicana de Valores   | Mexico    |
| `.SN`    | Santiago Stock Exchange     | Chile     |
| `.LIM`   | Lima Stock Exchange         | Peru      |

### Canada

| Suffix | Exchange               | Country |
| ------ | ---------------------- | ------- |
| `.TO`  | Toronto Stock Exchange | Canada  |
| `.V`   | TSX Venture Exchange   | Canada  |

### Europe

| Suffix | Exchange                 | Country     |
| ------ | ------------------------ | ----------- |
| `.L`   | London Stock Exchange    | UK          |
| `.PA`  | Euronext Paris           | France      |
| `.AS`  | Euronext Amsterdam       | Netherlands |
| `.BR`  | Euronext Brussels        | Belgium     |
| `.LS`  | Euronext Lisbon          | Portugal    |
| `.MI`  | Borsa Italiana           | Italy       |
| `.DE`  | XETRA                    | Germany     |
| `.F`   | Frankfurt Stock Exchange | Germany     |
| `.SW`  | SIX Swiss Exchange       | Switzerland |
| `.ST`  | Nasdaq Stockholm         | Sweden      |
| `.HE`  | Nasdaq Helsinki          | Finland     |
| `.CO`  | Nasdaq Copenhagen        | Denmark     |
| `.OL`  | Oslo Børs                | Norway      |
| `.WA`  | Warsaw Stock Exchange    | Poland      |
| `.AT`  | Athens Stock Exchange    | Greece      |

> Yahoo quotes London tickers in pence (`GBp`). stonks-cli converts those
> prices to pounds, so enter `avg_cost` in pounds and set `currency: GBP`.
> The same applies to Johannesburg (`ZAc`, cents) and Tel Aviv (`ILA`,
> agorot) quotes.

### Asia-Pacific

| Suffix  | Exchange                       | Country     |
| ------- | ------------------------------ | ----------- |
| `.AX`   | Australian Securities Exchange | Australia   |
| `.NZ`   | New Zealand Exchange           | New Zealand |
| `.HK`   | Hong Kong Stock Exchange       | Hong Kong   |
| `.T`    | Tokyo Stock Exchange           | Japan       |
| `.KS`   | Korea Exchange (KOSPI)         | South Korea |
| `.KQ`   | KOSDAQ                         | South Korea |
| `.TW`   | Taiwan Stock Exchange          | Taiwan      |
| `.TWO`  | Taiwan OTC                     | Taiwan      |
| `.SS`   | Shanghai Stock Exchange        | China       |
| `.SZ`   | Shenzhen Stock Exchange        | China       |
| `.NS`   | National Stock Exchange        | India       |
| `.BO`   | Bombay Stock Exchange          | India       |
| `.JK`   | Indonesia Stock Exchange       | Indonesia   |
| `.SI`   | Singapore Exchange             | Singapore   |
| `.KL`   | Bursa Malaysia                 | Malaysia    |
| `.BK`   | Stock Exchange of Thailand     | Thailand    |
| `.VN`   | Ho Chi Minh Stock Exchange     | Vietnam     |

### Cryptocurrency

Crypto symbols follow Yahoo Finance's `BASE-QUOTE` convention (e.g. `BTC-USD`).
Prices are fetched from the **CoinGecko public API** instead of Yahoo Finance.
No API key is required; set `COINGECKO_DEMO_API_KEY` in your environment to
use a Demo key for higher rate limits.

Set `asset_type: crypto` and provide an `external_id` (the CoinGecko coin ID)
for each crypto position or watchlist item. The `external_id` disambiguates
symbols that map to multiple coins and avoids a runtime search API call.

| Name      | Yahoo Finance symbol | CoinGecko `external_id` |
| --------- | -------------------- | ----------------------- |
| Bitcoin   | `BTC-USD`            | `bitcoin`               |
| Ethereum  | `ETH-USD`            | `ethereum`              |
| Solana    | `SOL-USD`            | `solana`                |
| Cardano   | `ADA-USD`            | `cardano`               |
| Dogecoin  | `DOGE-USD`           | `dogecoin`              |
| Polygon   | `MATIC-USD`          | `matic-network`         |
| Chainlink | `LINK-USD`           | `chainlink`             |
| Uniswap   | `UNI-USD`            | `uniswap`               |

If `external_id` is omitted, stonks-cli resolves the coin automatically using
a bundled symbol map (11 000+ entries) and falls back to the CoinGecko
`/search` endpoint for ambiguous symbols, ranked by market cap.

### Bonds

Add a `bond` block to a position to track an individual bond. `quantity` is
the number of bonds and `avg_cost` is the clean purchase price in percent of
par, as brokers quote it.

```yaml
portfolio:
  positions:
    - symbol: UST5.0SEP31        # any unique label
      quantity: 15               # number of bonds
      avg_cost: 100.47           # clean price, percent of par
      currency: USD
      bond:
        maturity: 2031-09-30
        coupon: 5.0              # annual rate, percent
        cusip: 91282CRN3         # enables automatic Treasury pricing
        # face_value: 1000       # par per bond (default 1000)
        # frequency: 2           # coupons per year: 1, 2, 4 or 12 (default 2)
        # price: 100.47          # manual clean price; overrides fetched prices
```

| Field        | Type   | Required | Description                                              |
| ------------ | ------ | -------- | -------------------------------------------------------- |
| `maturity`   | date   | yes      | Maturity date, `YYYY-MM-DD`                              |
| `coupon`     | float  | yes      | Annual coupon rate in percent (`0` for zero-coupon)      |
| `face_value` | float  | no       | Par amount of one bond; defaults to `1000`               |
| `frequency`  | int    | no       | Coupons per year: `1`, `2`, `4` or `12`; defaults to `2` |
| `cusip`      | string | no       | Nine-character CUSIP                                     |
| `price`      | float  | no       | Manual clean price in percent of par                     |

Each bond is priced from the first source that works. The Exchange column
shows which one was used.

| Source                                                         | Label       | Applies to                              |
| -------------------------------------------------------------- | ----------- | --------------------------------------- |
| Manual `price` from the YAML                                   | `Manual`    | Any bond                                |
| [Treasury FedInvest][fedinvest] end-of-day price for the CUSIP | `UST`       | U.S. Treasuries (CUSIP starting `912`)  |
| Model price from the [Treasury par yield curve][par-curve]     | `UST model` | U.S. Treasuries not listed by FedInvest |

Both Treasury sources publish once a day, so prices are fetched at most once
an hour. FedInvest posts the current day's prices after about 1 p.m. Eastern;
until then the previous business day's price is shown. Bonds that are not
U.S. Treasuries need a manual `price`.

Market value is the clean value plus accrued interest, which is what the
position would fetch if sold. Unrealized P&L uses the clean price only, so
accrued interest is not counted as a gain. Bonds are left out of backtests,
since there is no price history for individual bonds.

[fedinvest]: https://www.treasurydirect.gov/GA-FI/FedInvest/todaySecurityPriceDate
[par-curve]: https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve

> Bond ETFs such as `TLT` are ordinary tickers. Add them without a `bond`
> block; `asset_type: bond` on its own is just a label.

### Examples

| Symbol       | Instrument                |
| ------------ | ------------------------- |
| `AAPL`       | Apple (NASDAQ)            |
| `ASML.AS`    | ASML (Euronext Amsterdam) |
| `7203.T`     | Toyota (Tokyo SE)         |
| `HSBA.L`     | HSBC (London SE)          |
| `005930.KS`  | Samsung (KOSPI)           |
| `BTC-USD`    | Bitcoin / USD             |
| `ETH-USD`    | Ethereum / USD            |

---

## Usage

```text
stonks [OPTIONS] COMMAND [ARGS]...

Options:
  -p, --portfolio PATH                    Portfolio YAML file or name
                                          (repeatable; default:
                                          ~/.config/stonks/portfolio.yaml)
  --log-level [DEBUG|INFO|WARNING|ERROR]  Log verbosity written to the log
                                          file  [default: WARNING]
  -V, --version                           Show the version and exit.
  --help                                  Show this message and exit.

Commands:
  add         Add QUANTITY shares of SYMBOL at PRICE to the portfolio.
  remove      Remove QUANTITY shares of SYMBOL from the portfolio.
  add-cash    Add AMOUNT of CURRENCY cash to the portfolio.
  remove-cash Remove AMOUNT of CURRENCY cash from the portfolio.
  watchlist   Manage symbols tracked for price only (subcommands: add, remove, list).
  set         Change portfolio settings (subcommands: name, base-currency).
  show        Print a snapshot of portfolio positions with current prices to stdout.
  dashboard   Display the current portfolio with live prices and P&L.
  list        List all portfolios in ~/.config/stonks/.
  demo        Launch the TUI with a sample portfolio (your portfolio is untouched).
  feed        Print the latest news headlines for a symbol to stdout.
  detail      Print a financial summary for a symbol to stdout.
  doctor      Run environment health checks.
  import      Import positions from a broker export (subcommands: ibkr).
```

`-p` may be repeated for the read-only commands (`show`, `dashboard`,
`doctor`, `watchlist list`) to work with several portfolios at once.
Commands that modify a portfolio (`add`, `remove`, `add-cash`, `remove-cash`,
`watchlist add`/`remove`, `set`, `import`) accept exactly one `-p` and exit
with an error if more are given.

### Quick snapshot (stdout)

Print the current portfolio state with live prices to stdout and exit:

```bash
# One-shot table output (no TUI)
stonks show

# With a specific portfolio
stonks -p work show
```

The output includes the same columns as the dashboard (Instrument, Exchange,
Qty, Avg Cost, Last Price, Daily Chg, Daily Chg %, Mkt Value, Unrealized P&L)
plus a Total line.
Session badges (PRE/AH/CLS) are appended to the last price when applicable.
If a price cannot be fetched, `N/A` is shown instead.

### Interactive dashboard (TUI)

Running `stonks` without a subcommand launches the dashboard automatically.
Prices refresh every **60 seconds** by default.

```bash
# Launch the TUI (dashboard is the default command)
stonks

# Equivalent -- explicit subcommand
stonks dashboard

# Refresh prices every 30 seconds instead
stonks dashboard --refresh 30

# Open two portfolios side by side
stonks -p personal -p work
```

### Add a position

```bash
# Add 10 shares of Apple at $150.00
stonks add AAPL 10 150.00

# Add a non-US stock (ASML on Euronext Amsterdam)
stonks add ASML.AS 5 680.00

# Add crypto: a BASE-USD symbol is tagged asset_type: crypto automatically.
# Fractional quantities are fine.
stonks add BTC-USD 0.5 60000.00

# The CoinGecko coin ID is filled in from the bundled map when the ticker is
# unambiguous; otherwise it is looked up via CoinGecko search at refresh time.
# To pin it (or set the currency / asset type) explicitly:
stonks add BTC-USD 0.5 60000.00 --external-id bitcoin
stonks add ASML.AS 5 680.00 --currency EUR --asset-type equity

# Use a named portfolio (resolves to ~/.config/stonks/work.yaml)
stonks -p work add NVDA 2 800.00
```

When a symbol is added a second time, the quantity is increased and the
average cost is recalculated as a weighted average automatically.

### Remove a position

```bash
# Remove 5 shares (partial close)
stonks remove AAPL 5

# Remove all shares (position deleted)
stonks remove AAPL 10
```

### Manage cash

```bash
# Deposit cash
stonks add-cash USD 5000.00
stonks add-cash EUR 2000.00

# Withdraw cash (partial)
stonks remove-cash USD 500.00

# Withdraw all remaining cash of a currency (position deleted)
stonks remove-cash EUR 2000.00
```

Cash is displayed in the dashboard below the stock positions and is included
in the total portfolio value converted to the base currency.

### Manage the watchlist

```bash
# Track a symbol without holding a position
stonks watchlist add TSLA

# Crypto: route pricing via CoinGecko and pin the coin ID
stonks watchlist add LINK-USD --asset-type crypto --external-id chainlink

# Stop tracking a symbol
stonks watchlist remove TSLA

# Print the watchlist (works with several -p flags)
stonks watchlist list
```

Watchlist rows appear dimmed in the dashboard and are not counted in the
portfolio total.

### Portfolio settings

```bash
# Label shown in the dashboard and in multi-portfolio output
stonks -p work set name "Work account"
stonks -p work set name --clear

# Currency used for the Total line (three-letter ISO 4217 code)
stonks set base-currency EUR
```

### List portfolios

```bash
stonks list
```

Lists all `.yaml` files found in `~/.config/stonks/`.

### Importing positions

Positions can be bulk-imported from broker exports instead of being added one
by one.

| Broker | Command | Guide |
|---|---|---|
| Interactive Brokers | `stonks import ibkr positions.csv` | [docs/import/interactive-brokers.md](docs/import/interactive-brokers.md) |

### Health checks (doctor)

Run `stonks doctor` to verify that the environment is configured correctly
before opening the TUI:

```bash
stonks doctor

# Check a specific portfolio
stonks -p work doctor

# Check multiple portfolios at once
stonks -p personal -p work doctor
```

Each check prints a coloured `OK`, `WARN`, or `FAIL` line. The command exits
with code `0` when everything passes and code `1` if any hard check fails.

| Check | What it verifies |
| ----- | ---------------- |
| Python version | Interpreter is 3.11 or newer |
| stonks-cli version | Installed version vs. latest PyPI release |
| exchange-calendars | NYSE calendar loads and today's session is readable |
| Portfolio file | File exists and parses without errors; reports position/cash/watchlist counts |
| yfinance | Fetches AAPL `fast_info` as a live connectivity probe |
| CoinGecko | Hits `/ping`; warns if `COINGECKO_DEMO_API_KEY` is unset |
| Forex rates | Fetches a live EUR/USD rate via yfinance |
| Symbol validation | Probes each ticker in the portfolio; equity in parallel, crypto via CoinGecko batch |

Symbol validation is skipped for equity symbols when yfinance is unreachable,
and for crypto symbols when CoinGecko is unreachable, to avoid a flood of
redundant errors.

---

## Dashboard

### Columns

| Column         | Description                                                          |
| -------------- | -------------------------------------------------------------------- |
| Instrument     | Ticker symbol                                                        |
| Exchange       | Exchange name derived from ticker suffix (e.g. NYSE/NASDAQ, Crypto)  |
| Qty            | Number of shares held                                                |
| Avg Cost       | Average purchase price per share                                     |
| Last Price     | Most recent price; tagged PRE, AH, or CLS for non-regular sessions   |
| Daily Chg      | Absolute price change vs. the previous close (green / red)           |
| Daily Chg %    | Percentage change vs. the previous close (green / red)               |
| Mkt Value      | Current market value (Qty * Last Price)                              |
| Unrealized P&L | Profit/loss vs. average cost (bold green / bold red)                 |

Both daily change columns show `--` for closed-session tickers (CLS).

A **Total** line at the bottom converts all positions and cash to the
portfolio's base currency using live forex rates.

Watchlist rows are dimmed and only show Instrument, Exchange, Last Price, and
Daily Chg columns -- all other columns display `--`.

Column widths are distributed proportionally and reflow automatically when
the terminal is resized.

### Session labels

US equities on exchanges that support extended hours show a session tag next
to the price:

| Tag      | Session                                                              |
| -------- | -------------------------------------------------------------------- |
| `PRE`    | Pre-market                                                           |
| `AH`     | After-hours / post-market                                            |
| `CLS`    | Market closed                                                        |
| `STALE`  | Exchange is open but the data source only has an earlier day's price |
| *(none)* | Regular trading hours                                                |

Non-US equities and crypto show no tag during regular hours and `CLS` when the
market is closed (using holiday-aware calendar data). A `STALE` row shows `--`
for the daily change rather than a misleading `0.00`.

### Keyboard shortcuts

| Key     | Action                                              |
| ------- | --------------------------------------------------- |
| `a`     | Add a position, cash balance or watchlist item      |
| `e`     | Edit the selected position, cash or watchlist row   |
| `r`     | Remove the selected position, cash or watchlist row |
| `Enter` | Open the detail screen for the selected row         |
| `f`     | Open the detail screen for the selected row         |
| `g`     | Open the candlestick chart (not for bonds)          |
| `Tab`   | Switch focus between portfolio tables               |
| `b`     | Run a portfolio backtest                            |
| `n`     | Toggle the news feed panel                          |
| `l`     | Open the log viewer                                 |
| `q`     | Quit                                                |

Column headers are clickable -- click once to sort ascending, again to sort
descending.

---

## News feed

Press **`n`** from the dashboard to toggle the news feed panel. It shows the
most recent headlines across all your portfolio symbols and watchlist items,
sourced from Yahoo Finance. Articles older than 3 days are filtered out
automatically.

Each row shows the publication time, symbol, headline, and source. Click a
headline to open it in your browser.

---

## Candlestick chart

Press **`g`** on any equity or watchlist row to open an interactive OHLC
candlestick chart directly in the terminal. Press **Escape** or **Q** to
return to the dashboard.

### Time ranges

| Label    | Period | Interval |
| -------- | ------ | -------- |
| `1D 1m`  | 1 day  | 1 minute  |
| `1D 2m`  | 1 day  | 2 minutes |
| `5D 5m`  | 5 days | 5 minutes |
| `1M 15m` | 1 month | 15 minutes |
| `3M 1h`  | 3 months | 1 hour  |
| `6M 1d`  | 6 months | 1 day   |
| `1Y 1d`  | 1 year  | 1 day   |
| `5Y 1wk` | 5 years | 1 week  |

The default view opens at **5D 5m**.

### Keyboard shortcuts

| Key          | Action                                 |
| ------------ | -------------------------------------- |
| `Left`       | Move cursor to the previous candle     |
| `Right`      | Move cursor to the next candle         |
| `Home`       | Jump to the first (oldest) candle      |
| `End`        | Jump to the last (newest) candle       |
| `+` / `=`    | Zoom in (narrower interval)            |
| `-` / `_`    | Zoom out (wider interval)              |
| `Up`         | Expand Y-axis range                    |
| `Down`       | Narrow Y-axis range                    |
| `Escape` / `Q` | Close and return to the dashboard    |

When you reach the leftmost or rightmost candle, the chart automatically
prefetches older or newer data so you can scroll continuously through history.
Zooming recentres on the candle under the cursor.

---

## Stock detail screen

Press **Enter** on any equity or watchlist row to open a full-screen detail
view. Press **Escape** or **Q** to return to the dashboard.

On a bond row, **Enter** or **f** opens a bond view instead. It shows the
terms, the price source, the yield to maturity, and the clean value, accrued
interest and market value. See [Bonds](#bonds).

The detail screen displays:

- **Performance** -- trailing returns (YTD, 1Y, 3Y, 5Y) compared side-by-side
  with the S&P 500
- **Price charts** -- interactive terminal charts for 1 Day, 1 Month, 1 Year,
  and 5 Years
- **Financial summary** -- previous close, open, bid/ask, day range, 52-week
  range, volume, market cap, P/E, EPS, earnings date, dividend yield
- **Earnings trends** -- quarterly EPS actual vs. estimate bar chart; revenue
  vs. net income chart
- **Analyst insights** -- price targets (low / mean / high), consensus rating,
  and a monthly recommendations breakdown (Strong Buy to Strong Sell)
- **Statistics** -- valuation measures and financial highlights

---

## Logging

stonks-cli writes structured log messages to a platform-specific file:

| Platform | Path                                          |
| -------- | --------------------------------------------- |
| Linux    | `~/.local/state/stonks/log/stonks.log`        |
| macOS    | `~/Library/Logs/stonks/stonks.log`            |
| Windows  | `%LOCALAPPDATA%\stonks\Logs\stonks.log`       |

The default verbosity is `WARNING` (only warnings and errors are recorded).
Use `--log-level` to capture more detail:

```bash
# Capture all debug output
stonks --log-level DEBUG

# Capture info and above
stonks --log-level INFO dashboard --refresh 30
```

Log entries follow the format:

```text
YYYY-MM-DDTHH:MM:SS  LEVEL     logger_name  message
```

---

## Running with Docker

### Build the image

```bash
docker build -t stonks .
```

### Run the container

```bash
docker run --rm -it \
  -v ./config/sample_portfolio.yaml:/data/portfolio.yaml:ro \
  stonks --portfolio /data/portfolio.yaml dashboard
```

The `-v` flag bind-mounts a local YAML file into the container. Replace
`./config/sample_portfolio.yaml` with the path to your own portfolio file.
Drop `:ro` if you want `add` / `remove` commands to persist changes back to
the host.

---

## Backtesting

Press **`b`** from the dashboard to open the backtest configuration form.
Backtesting simulates how your portfolio's current allocation would have
performed over a historical period, compared against a benchmark index.

### Configuration

| Field            | Default | Description                                            |
| ---------------- | ------- | ------------------------------------------------------ |
| Benchmark Symbol | `SPY`   | Ticker to compare against (e.g. `SPY`, `QQQ`, `VT`)   |
| Starting Amount  | `10000` | Initial investment amount in USD                       |
| Start Year       | `2010`  | First year of the simulation                           |
| End Year         | current | Last year of the simulation                            |
| Yearly Cashflows | `0`     | Additional amount contributed each year                |
| Rebalancing      | none    | `none`, `monthly`, or `annual` rebalancing strategy    |
| Skip unavailable | on      | Skip positions without historical data for the period  |

The portfolio's current allocation weights are held constant throughout the
simulation. Historical prices are fetched from Yahoo Finance.

### Results

The backtest results screen displays:

- **Portfolio Growth chart** -- cumulative value of the portfolio vs. the
  benchmark over the full period
- **Annual Returns chart** -- side-by-side grouped bar chart showing yearly
  returns for the portfolio and benchmark, with annotated values
- **Summary statistics** -- CAGR, max drawdown, Sharpe ratio, best/worst year,
  and final portfolio value for both the portfolio and the benchmark

If any position symbols lack historical data for the requested start year and
**Skip unavailable** is unchecked, an error message is shown with the earliest
available date for each symbol. When the checkbox is enabled (the default),
those positions are excluded automatically and their weights are redistributed
among the remaining symbols. Skipped positions are listed at the top of the
results screen. The benchmark symbol is always required regardless of this
setting.

Press **Escape** or **Q** to return to the dashboard.

---

## Market analysis

![stonks](docs/assets/stonks.png)

Looks bullish.

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for
development setup, coding style, commit message conventions, and the pull
request workflow.

For the release process, see [RELEASING.md](RELEASING.md).

---

## License

MIT License. See [LICENSE](LICENSE) for details.
