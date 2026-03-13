# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/igorpaniuk/stonks-cli/releases/tag/v0.1.0
