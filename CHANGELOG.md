# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-03-09

### Added
- Added unified trading-calendar utilities in [core/trading_calendar.py](core/trading_calendar.py), including trading-day checks, market-day offsets, and trading-day range helpers.
- Added configurable fee schedule support via [config/fee_schedule.py](config/fee_schedule.py) and the template file [config/fee_rates.csv](config/fee_rates.csv).
- Added fee tracking fields for trades and positions, including buy commission, sell commission, stamp tax, total fees, and `T+0/T+1` metadata.
- Added dashboard fee summary cards in the frontend to display total fees, buy commissions, sell commissions, stamp tax, and realized PnL.
- Added regression tests for trading-calendar helpers, fee schedule loading, fee rounding, `T+0/T+1` position availability, and fee persistence/API exposure.
- Added a more general-purpose history-data module with batch download, independent cache reads, selectable fields, fill behavior control, and progress display support.

### Changed
- Moved legacy `date.py` functionality into `core` and kept `date.py` as a compatibility wrapper.
- Updated `StrategyRunner` to only activate strategies on trading days and to skip non-trading-day stock selection.
- Updated `OrderManager` to calculate per-trade fees automatically from the configured fee schedule and accumulate fees at the order level.
- Updated `PositionManager` to:
	- include fees in cost basis and realized PnL,
	- track cumulative fee breakdown,
	- enforce `T+1` available quantity rules for ordinary securities,
	- support `T+0` same-day re-sell for configured funds/ETFs.
- Extended SQLite trade persistence and API responses to expose fee breakdown and `is_t0` information.
- Updated frontend positions, trades, and dashboard pages to show fee and `T+0/T+1` information.
- Updated README to document trading-day control, fee schedule configuration, deployment updates, and the latest UI/API capabilities.
- Refactored [core/history_data.py](core/history_data.py) to separate download and read responsibilities while keeping `get_history_data()` as a compatibility wrapper.
- Switched historical batch download to `xtdata.download_history_data2(...)` and added `tqdm` progress reporting support.

### Verified
- Full Python test suite passes: `84 passed`.
- Frontend production build passes via `npm run build`.

## [0.1.0] - 2026-03-06

### Added
- Added open-source readiness files: `.gitignore`, `.env.example`, `CONTRIBUTING.md`, `SECURITY.md`, `RELEASE_CHECKLIST.md`.
- Added regression tests for main app wiring, data subscription recovery, web cancel route, settings environment overrides, and `xt_order_id` persistence/migration.
- Added `终审.md` as the final review summary.

### Changed
- Improved `README.md` for public/open-source usage.
- Moved settings toward environment-variable-first configuration.
- Unified `xt_order_id` persistence to integer storage and added migration handling for legacy SQLite schemas.
- Cleaned up duplicate `resubscribe_all()` implementation in data subscription manager.
- Fixed reconnect callback registration in the main entry wiring.
- Updated project docs to remove sensitive examples and align review/test baseline.

### Verified
- Full test suite passes: `50 passed`.
