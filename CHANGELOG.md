# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-03-27

### Added

- **`stonks show` command** -- one-shot portfolio snapshot with live prices
  printed to stdout; supports multi-portfolio headers, session badges
  (PRE/AH/CLS), Daily chg, and a Total line in the base currency.
  Contributed by @MrCry0.
- **Cryptocurrency support** -- positions and watchlist items with
  `asset_type: crypto` are priced via the CoinGecko public API (no key
  required). New `asset_type` and `external_id` fields on positions and
  watchlist entries; a bundled 11 000+ entry coin map covers most symbols
  without a runtime API call. Set `COINGECKO_DEMO_API_KEY` for higher rate
  limits.
- **Daily % change column** -- shows intraday gain/loss vs. the previous
  close for every position and watchlist row; green for gains, red for
  losses; suppressed (`--`) for closed sessions.
- **Log viewer screen** -- press `L` in the dashboard to open the current
  log file in a full-screen TUI view without leaving the app.
- **`--log-level` CLI option** -- control logging verbosity at startup
  (`DEBUG`, `INFO`, `WARNING`, `ERROR`; default: `WARNING`).
- **Per-process log file** -- each `stonks` instance writes to
  `stonks.<pid>.log` in the platform log directory so concurrent instances
  never contend on the same file. Files older than 30 days are removed
  automatically at startup.
- `asset_type` dropdown and `external_id` input added to the Add/Edit
  position and watchlist forms in the TUI.

### Fixed

- `CLS` session label now shown correctly for non-trading tickers (e.g.
  crypto outside market hours); daily change is suppressed for closed
  sessions instead of showing a stale value.
- `TypeError` in `fetch_previous_closes` caused by mixing tz-aware and
  tz-naive datetime objects when building the lookback window.
- Watchlist and position rows are now correctly distinguished in the
  edit/remove TUI handlers.
- Log messages in the fetcher reformatted for improved readability
  (symbols logged without extra quoting, list reprs replaced with joined
  strings).

## [0.3.1] - 2026-03-23

### Fixed

- Dashboard can now open successfully for portfolios that contain only
  watchlist items and no held positions.
- Default dashboard refresh interval increased to 60 seconds to reduce
  unnecessary polling and lower the risk of hitting upstream rate limits.
- Non-ASCII typographic symbols introduced by editors or copied rich text were
  replaced with ASCII-safe equivalents across repository text and UI strings.

### Changed

- CI and pre-commit now run a Unicode normalization check that flags common
  LLM-style non-ASCII symbols and invisible whitespace before changes are
  accepted.

## [0.3.0] - 2026-03-19

### Added

- **Stock detail screen**: press Enter on an equity row to open a full-screen
  detail view for the selected ticker, showing:
  - Performance overview (YTD, 1Y, 3Y, 5Y trailing returns vs S&P 500)
  - Price charts for multiple periods (1 Day, 1 Month, 1 Year, 5 Years)
  - Financial summary (Previous Close, Open, Bid/Ask, Day's Range, 52-Week
    Range, Volume, Market Cap, P/E, EPS, Earnings Date, Dividend, etc.)
  - Earnings trends: EPS actual vs estimate bar chart with next-quarter
    estimate, and Revenue vs Net Income quarterly chart
  - Analyst insights: price targets, recommendation key, and stacked bar chart
    of analyst recommendations by month
  - Statistics: valuation measures and financial highlights
- Full company/fund name displayed below the detail screen header.
- `textual-plotext` dependency for terminal-native line and bar charts.
- **Watchlist support**: new `watchlist` section in the portfolio YAML for
  tracking tickers without holdings. Watchlist rows appear in the dashboard
  with dimmed styling, show live prices only, and are excluded from the
  portfolio total.
- Add/edit/remove watchlist items via `a`/`e`/`r` keyboard shortcuts with a
  symbol-only form dialog.

### Fixed

- Cursor position preserved after table refresh.
- Currency preserved when adding shares to an existing position.
- **macOS "Too many open files" crash**: `yf.download()` internally spawns
  thread-pool workers that leak `curl_cffi` HTTP sessions. All download calls
  now pass `threads=False` to reuse a single session, a non-blocking lock
  prevents overlapping refresh cycles, and the exchange-name worker pool is
  reduced from 8 to 2.

### Changed

- Column headers are now clickable to sort rows.
- Add/edit/remove positions via keyboard shortcuts (a/e/r).
- Tab shortcut shown in the footer status bar.

## [0.2.1] - 2026-03-16

### Fixed

- Non-US tickers (e.g. `6758.T`, `7203.T`) always showing no session label
  despite being outside trading hours. `fetch_extended_prices` now derives the
  session from the current wall-clock time via `current_session()` rather than
  the yfinance bar timestamp, which is always within regular hours for exchanges
  that have no extended-hours data.
- `TypeError: 'NoneType' object is not subscriptable` crash when fetching
  prices for illiquid or delisted tickers (e.g. `KSG.WA`) via the individual
  fallback path. `TypeError` is now caught alongside `ValueError`, `KeyError`,
  and `AttributeError` in `fetch_price_single`.
- Unhandled crash in `fetch_extended_prices` when the batch contains a mix of
  equity and forex tickers. `yf.download()` internally calls `pd.concat()`,
  which raises `ValueError` (or `TypeError`) when tz-aware daily forex bars
  are concatenated with tz-naive minute equity bars. The exception is now
  caught so the fetch falls back gracefully to the tier-2/tier-3 paths.
- Non-US tickers (e.g. `6758.T`, `IWDA.AS`) incorrectly showing `PRE` or
  `AH` outside trading hours. Non-US exchanges do not offer extended-hours
  trading via yfinance, so `current_session()` now returns `CLS` whenever
  the wall-clock time falls outside regular session hours for any exchange
  that does not have `extended_hours` enabled. Only US exchanges (NASDAQ,
  NYSE, Arca, CBOE, OTC) retain the `PRE`/`AH` labels.

## [0.2.0] - 2026-03-16

### Added

- Dashboard is now the default command: running `stonks` without a subcommand
  opens the TUI automatically.
- First-run bootstrapping: if `~/.config/stonks/` contains no `.yaml` files, a
  sample portfolio is created automatically so the dashboard is usable
  immediately after installation.
- Exchange column in the portfolio table shows the exchange name derived from
  the ticker suffix (e.g. `NYSE/NASDAQ`, `Euronext Amsterdam`, `Crypto`).
- Holiday-aware exchange open/closed detection using `exchange-calendars`.
- Extended-hours session labels in the Last Price column: `PRE` (pre-market),
  `AH` (after-hours), `CLS` (closed).
- `--version` / `-V` flag.
- Codecov coverage reporting in CI.

### Changed

- Portfolio total shows `N/A` instead of a partial sum when any position price
  or required forex rate is unavailable.
- "Obtaining market data..." message moved to a dedicated status bar above the
  footer.
- yfinance minimum version bumped to `^1.2.0`.

### Fixed

- Sporadic `RuntimeError: dictionary changed size during iteration` crash caused
  by yfinance's internal worker threads mutating shared state during iteration.
  Each `yf.download()` call is now wrapped in `try/except RuntimeError`; on a
  collision the refresh cycle is skipped silently and retried on the next
  interval.
- European and illiquid tickers (e.g. `VUAA.L`, `IWDA.AS`, `CHIP.PA`) showing
  `N/A` in the dashboard. A three-tier price fetch pipeline was introduced:
  1-minute extended-hours batch -> daily batch fallback -> individual
  `yf.Ticker.fast_info` lookup, which bypasses cross-exchange DataFrame
  alignment issues entirely.
- Stocks outside regular trading hours (e.g. IBM pre-market) incorrectly showing
  `CLS` instead of `PRE` or `AH`. The session gate now uses a calendar-aware
  trading-day check (`_is_trading_day`) rather than `_is_exchange_open`, so the
  bar timestamp is always evaluated against the correct session window.
- Symbols resolved via the daily-batch or individual fallback paths showing no
  session label in the dashboard. They now call `current_session()`, which
  derives the correct label (`PRE`, `AH`, `CLS`, or regular) from the current
  wall-clock time.

## [0.1.0] - 2026-03-13

### Added

- Support for multiple portfolios
- Cash position support via `add-cash` / `remove-cash` commands
- Configurable total portfolio currency via YAML
- Pre-market and after-hours pricing surfaced in the dashboard
- Consolidated total across all portfolio currencies
- Configurable price refresh interval
- Interactive TUI dashboard (`dashboard` command)
- Portfolio management exposed through a CLI
- Live market price fetching to value the portfolio
- Persistent portfolio positions across CLI sessions
- Dockerfile for containerised deployment
- Pending status display while initial prices are loading
- Dataclasses for `Position` and `Portfolio`
- CI workflows: sanity checks, conventional commits check, markdown linter
- Pre-commit hooks configuration
- Development tools and CI check script

### Fixed

- Guard against worker callback firing after app teardown

### Changed

- Renamed `show` command to `dashboard`

[0.4.0]: https://github.com/igoropaniuk/stonks-cli/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/igoropaniuk/stonks-cli/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/igoropaniuk/stonks-cli/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/igoropaniuk/stonks-cli/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/igoropaniuk/stonks-cli/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/igoropaniuk/stonks-cli/releases/tag/v0.1.0
