# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
  1-minute extended-hours batch → daily batch fallback → individual
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

[0.2.0]: https://github.com/igoropaniuk/stonks-cli/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/igoropaniuk/stonks-cli/releases/tag/v0.1.0
