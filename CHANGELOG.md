# Changelog / 更新日志

All notable changes to this project will be documented in this file.

本项目的重要变更都会记录在此文件中。

## [0.3.1] - 2026-03-12

### Added / 新增
- Added daily session control settings in [config/settings.py](config/settings.py), including `SESSION_START_TIME`, `SESSION_EXIT_TIME`, `SESSION_POLL_INTERVAL_SEC`, and `LOAD_PREVIOUS_STATE_ON_START`.
	在 [config/settings.py](config/settings.py) 中新增日内会话控制配置，包括 `SESSION_START_TIME`、`SESSION_EXIT_TIME`、`SESSION_POLL_INTERVAL_SEC` 和 `LOAD_PREVIOUS_STATE_ON_START`。
- Added previous-trading-day state fallback loading in [data/manager.py](data/manager.py) so startup can restore the latest available snapshot when the current trading day has no state file yet.
	在 [data/manager.py](data/manager.py) 中新增上一交易日状态回退加载能力，使程序在当日还没有状态文件时，可以自动恢复最近可用快照。
- Added scheduler-oriented runtime entrypoints in [main.py](main.py), including parent-process scheduling, subprocess session launching, and a dedicated managed trading-session lifecycle.
	在 [main.py](main.py) 中新增面向调度运行的主入口，包括父进程调度、子进程交易会话拉起以及完整的受控交易日会话生命周期。
- Added regression tests covering daily session control, previous-day snapshot fallback, and scheduler wiring in [tests/test_main.py](tests/test_main.py), [tests/test_data_manager.py](tests/test_data_manager.py), and [tests/test_settings.py](tests/test_settings.py).
	在 [tests/test_main.py](tests/test_main.py)、[tests/test_data_manager.py](tests/test_data_manager.py) 和 [tests/test_settings.py](tests/test_settings.py) 中新增覆盖日会话控制、上一交易日状态回退和调度器装配的回归测试。

### Changed / 变更
- Switched the default top-level runtime in [main.py](main.py) to a parent-process scheduler model based on `BlockingScheduler`, where each trading task launches in an isolated subprocess via `ProcessPoolExecutor`.
	将 [main.py](main.py) 的默认顶层运行模式切换为基于 `BlockingScheduler` 的父进程调度模型，并通过 `ProcessPoolExecutor` 为每次交易任务创建独立子进程。
- Updated the managed session shutdown flow in [main.py](main.py) so strategy runner, watchdog, data subscription, QMT connection, and data resources are closed together when the session ends.
	更新 [main.py](main.py) 中的受控会话关闭流程，使策略运行器、看门狗、行情订阅、QMT 连接和数据资源在会话结束时统一关闭。
- Updated [strategy/runner.py](strategy/runner.py) to honor configurable previous-day state fallback during startup restore.
	更新 [strategy/runner.py](strategy/runner.py)，使其在启动恢复状态时支持可配置的上一交易日快照回退逻辑。
- Expanded runtime documentation in [main.py](main.py) to explain the two-layer architecture of parent-process scheduling and subprocess trading sessions in detail.
	扩展 [main.py](main.py) 中的运行说明，详细解释“父进程调度 + 子进程交易会话”的两层运行架构。

### Verified / 验证
- Full Python test suite passes: `94 passed`.
	Python 全量测试通过：`94 passed`。

## [0.3.0] - 2026-03-10

### Added / 新增
- Added configurable account type support in [config/settings.py](config/settings.py) and [core/connection.py](core/connection.py), allowing `StockAccount` creation to use configured account categories instead of being fixed to `STOCK`.
	新增 [config/settings.py](config/settings.py) 与 [core/connection.py](core/connection.py) 中的账户类型配置支持，使 `StockAccount` 可根据配置创建，而不再固定为 `STOCK`。
- Added richer xtquant callback payload propagation in [core/callback.py](core/callback.py), including more complete `XtOrder` and `XtTrade` fields for internal processing, persistence, and debugging.
	在 [core/callback.py](core/callback.py) 中新增更完整的 xtquant 回调信息透传能力，补充了更多 `XtOrder` 和 `XtTrade` 字段，便于内部处理、持久化和问题排查。
- Added expanded order and trade metadata fields in [trading/models.py](trading/models.py), [data/manager.py](data/manager.py), and [web/backend/schemas.py](web/backend/schemas.py), including account metadata, raw xt fields, fee breakdown, and extended identifiers.
	在 [trading/models.py](trading/models.py)、[data/manager.py](data/manager.py) 和 [web/backend/schemas.py](web/backend/schemas.py) 中新增更完整的订单与成交元数据字段，包括账户信息、xt 原始字段、费用拆分和扩展标识信息。
- Added startup preflight account and position consistency checks in [strategy/runner.py](strategy/runner.py), with warning output and DingTalk alert integration wired from [main.py](main.py).
	在 [strategy/runner.py](strategy/runner.py) 中新增启动前账户与持仓一致性检查，并通过 [main.py](main.py) 接入告警输出和钉钉提醒。
- Added a CSV-driven example strategy in [strategy/csv_signal_strategy.py](strategy/csv_signal_strategy.py) with sample input file [config/example_strategy_signals.csv](config/example_strategy_signals.csv).
	新增基于 CSV 驱动的示例策略 [strategy/csv_signal_strategy.py](strategy/csv_signal_strategy.py)，并提供示例输入文件 [config/example_strategy_signals.csv](config/example_strategy_signals.csv)。
- Added new regression coverage for account type handling, startup preflight validation, CSV strategy behavior, and cumulative order fee logic.
	新增账户类型处理、启动前校验、CSV 策略行为以及订单累计费用逻辑的回归测试覆盖。

### Changed / 变更
- Updated [trading/order_manager.py](trading/order_manager.py) to recalculate commissions from cumulative per-order filled amount and synchronize fee deltas correctly as order updates arrive.
	更新 [trading/order_manager.py](trading/order_manager.py)，改为基于订单累计成交金额重新计算手续费，并在订单状态更新时正确同步费用增量。
- Updated API routes in [web/backend/routes.py](web/backend/routes.py) to expose the expanded order fields returned by the backend.
	更新 [web/backend/routes.py](web/backend/routes.py)，对外提供后端新增的完整订单字段。
- Updated [README.md](README.md) with callback summaries, callback relationship diagrams, module relationship summaries, module diagrams, strategy runtime flowcharts, and architecture illustrations.
	更新 [README.md](README.md)，补充回调信息汇总、回调关系图、模块关系汇总、模块关系图、策略运行流程图和系统架构示意图。
- Performed a repository-wide documentation hardening pass across core runtime, trading, position, strategy, monitor, and web modules to align docstrings and comments with PEP 257 style for future automated documentation generation.
	对核心运行时、交易、持仓、策略、监控和 Web 模块执行了全仓库文档强化整理，使 docstring 与注释风格更统一，并尽量符合 PEP 257，便于后续自动化生成项目文档。

### Verified / 验证
- Full Python test suite passes: `90 passed`.
	Python 全量测试通过：`90 passed`。

## [0.2.0] - 2026-03-09

### Added / 新增
- Added unified trading-calendar utilities in [core/trading_calendar.py](core/trading_calendar.py), including trading-day checks, market-day offsets, and trading-day range helpers.
	在 [core/trading_calendar.py](core/trading_calendar.py) 中新增统一的交易日历工具，包括交易日判断、交易日偏移和交易日区间辅助方法。
- Added configurable fee schedule support via [config/fee_schedule.py](config/fee_schedule.py) and the template file [config/fee_rates.csv](config/fee_rates.csv).
	通过 [config/fee_schedule.py](config/fee_schedule.py) 与模板文件 [config/fee_rates.csv](config/fee_rates.csv) 新增可配置费率表支持。
- Added fee tracking fields for trades and positions, including buy commission, sell commission, stamp tax, total fees, and `T+0/T+1` metadata.
	为成交与持仓新增费用跟踪字段，包括买入佣金、卖出佣金、印花税、总费用以及 `T+0/T+1` 元数据。
- Added dashboard fee summary cards in the frontend to display total fees, buy commissions, sell commissions, stamp tax, and realized PnL.
	在前端仪表盘中新增费用汇总卡片，用于展示总费用、买入佣金、卖出佣金、印花税和已实现盈亏。
- Added regression tests for trading-calendar helpers, fee schedule loading, fee rounding, `T+0/T+1` position availability, and fee persistence/API exposure.
	新增交易日历辅助方法、费率表加载、费用取整、`T+0/T+1` 持仓可用数量以及费用持久化/API 暴露相关回归测试。
- Added a more general-purpose history-data module with batch download, independent cache reads, selectable fields, fill behavior control, and progress display support.
	新增更通用的历史数据模块，支持批量下载、独立缓存读取、字段可选、补齐行为控制和进度展示。

### Changed / 变更
- Moved legacy `date.py` functionality into `core` and kept `date.py` as a compatibility wrapper.
	将旧版 `date.py` 的功能迁移到 `core` 中，同时保留 `date.py` 作为兼容层包装器。
- Updated `StrategyRunner` to only activate strategies on trading days and to skip non-trading-day stock selection.
	更新 `StrategyRunner`，仅在交易日激活策略，并在非交易日跳过选股流程。
- Updated `OrderManager` to calculate per-trade fees automatically from the configured fee schedule and accumulate fees at the order level.
	更新 `OrderManager`，根据配置费率自动计算逐笔成交费用，并在订单层面累计费用。
- Updated `PositionManager` to:
	更新 `PositionManager`，使其：
	- include fees in cost basis and realized PnL,
		将费用纳入持仓成本和已实现盈亏计算；
	- track cumulative fee breakdown,
		跟踪累计费用拆分；
	- enforce `T+1` available quantity rules for ordinary securities,
		对普通证券严格执行 `T+1` 可卖数量规则；
	- support `T+0` same-day re-sell for configured funds/ETFs.
		对已配置的基金/ETF 支持 `T+0` 当日回转卖出。
- Extended SQLite trade persistence and API responses to expose fee breakdown and `is_t0` information.
	扩展 SQLite 成交持久化与 API 响应，暴露费用拆分和 `is_t0` 信息。
- Updated frontend positions, trades, and dashboard pages to show fee and `T+0/T+1` information.
	更新前端持仓、成交和仪表盘页面，展示费用与 `T+0/T+1` 信息。
- Updated README to document trading-day control, fee schedule configuration, deployment updates, and the latest UI/API capabilities.
	更新 README，补充交易日控制、费率配置、部署更新以及最新 UI/API 能力说明。
- Refactored [core/history_data.py](core/history_data.py) to separate download and read responsibilities while keeping `get_history_data()` as a compatibility wrapper.
	重构 [core/history_data.py](core/history_data.py)，分离下载与读取职责，同时保留 `get_history_data()` 作为兼容包装方法。
- Switched historical batch download to `xtdata.download_history_data2(...)` and added `tqdm` progress reporting support.
	将历史数据批量下载切换为 `xtdata.download_history_data2(...)`，并新增 `tqdm` 进度展示支持。

### Verified / 验证
- Full Python test suite passes: `84 passed`.
	Python 全量测试通过：`84 passed`。
- Frontend production build passes via `npm run build`.
	前端生产构建通过：`npm run build`。

## [0.1.0] - 2026-03-06

### Added / 新增
- Added open-source readiness files: `.gitignore`, `.env.example`, `CONTRIBUTING.md`, `SECURITY.md`, `RELEASE_CHECKLIST.md`.
	新增面向开源发布的基础文件：`.gitignore`、`.env.example`、`CONTRIBUTING.md`、`SECURITY.md`、`RELEASE_CHECKLIST.md`。
- Added regression tests for main app wiring, data subscription recovery, web cancel route, settings environment overrides, and `xt_order_id` persistence/migration.
	新增主程序装配、数据订阅恢复、Web 撤单接口、配置环境变量覆盖以及 `xt_order_id` 持久化/迁移相关回归测试。
- Added `private/终审.md` as the final review summary.
	新增 `private/终审.md` 作为最终审查总结。

### Changed / 变更
- Improved `README.md` for public/open-source usage.
	改进 `README.md`，使其更适合公开/开源使用场景。
- Moved settings toward environment-variable-first configuration.
	将配置方式调整为优先使用环境变量。
- Unified `xt_order_id` persistence to integer storage and added migration handling for legacy SQLite schemas.
	将 `xt_order_id` 的持久化统一为整数存储，并增加对旧版 SQLite 表结构的迁移处理。
- Cleaned up duplicate `resubscribe_all()` implementation in data subscription manager.
	清理数据订阅管理器中重复实现的 `resubscribe_all()` 逻辑。
- Fixed reconnect callback registration in the main entry wiring.
	修复主程序入口装配中的重连回调注册问题。
- Updated project docs to remove sensitive examples and align review/test baseline.
	更新项目文档，移除敏感示例，并统一审查与测试基线。

### Verified / 验证
- Full test suite passes: `50 passed`.
	全量测试通过：`50 passed`。
