"""策略运行模块。

本模块是项目中连接“行情、策略、订单、持仓、状态恢复”的调度中心。
它不关心具体策略逻辑本身，而是负责让多个策略实例在统一规则下运行。
"""
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Type

from config.enums import AlertLevel, StrategyStatus
from core.models import TickData
from core.trading_calendar import is_market_day
from strategy.base import BaseStrategy
from strategy.models import StrategyConfig, StrategySnapshot
from monitor.logger import get_logger

logger = get_logger("system")


def _select_configs_in_subprocess(strategy_class):
    """在子进程中执行选股逻辑并返回配置列表。

    这样做的主要目的是把潜在耗时较长、且可能依赖外部计算的选股逻辑
    与主进程隔离开，降低阻塞主流程的风险。
    """
    strategy = strategy_class(StrategyConfig(), None, None)
    return strategy.select_stocks()


class StrategyRunner:
    """策略运行管理器。

    它负责统一调度所有策略对象，是策略层的总控中心。
    """

    def __init__(self, data_subscription=None, trade_executor=None,
                 position_manager=None, data_manager=None,
                 connection_manager=None,
                 strategy_classes: List[Type[BaseStrategy]] = None,
                 load_previous_state_on_start: bool = True,
                 latency_threshold_sec: float = 10.0,
                 process_threshold_ms: float = 200.0):
        """初始化策略运行器。

        Args:
            data_subscription: 行情订阅管理器。
            trade_executor: 交易执行器。
            position_manager: 持仓管理器。
            data_manager: 数据持久化管理器。
            connection_manager: 交易连接管理器，用于启动前账户校验。
            strategy_classes: 需要托管的策略类列表。
            load_previous_state_on_start: 当日状态不存在时，是否回退加载上一交易日状态。
            latency_threshold_sec: 行情延迟告警阈值，单位秒。
            process_threshold_ms: 单次策略处理耗时告警阈值，单位毫秒。
        """
        # ``_data_sub`` 负责向运行器推送最新行情数据。
        self._data_sub = data_subscription
        # ``_trade_exec`` 负责把策略信号翻译成真实下单动作。
        self._trade_exec = trade_executor
        # ``_position_mgr`` 负责查询和维护策略持仓。
        self._position_mgr = position_manager
        # ``_data_mgr`` 用于保存和恢复策略状态快照。
        self._data_mgr = data_manager
        # ``_connection_mgr`` 用于在启动前查询账户资产与持仓。
        self._connection_mgr = connection_manager
        # ``_strategy_classes`` 保存所有可参与自动选股/恢复的策略类。
        self._strategy_classes = strategy_classes or []
        # ``_load_previous_state_on_start`` 控制是否回退到上一交易日状态文件。
        self._load_previous_state_on_start = load_previous_state_on_start
        # ``_strategies`` 保存当前正在托管的策略实例列表。
        self._strategies: List[BaseStrategy] = []
        # ``_lock`` 保护策略列表在多线程环境下的增删改查。
        self._lock = threading.Lock()
        # ``_latency_threshold`` 是行情延迟告警阈值，单位秒。
        self._latency_threshold = latency_threshold_sec
        # ``_process_threshold_ms`` 是单次策略处理耗时阈值，单位毫秒。
        self._process_threshold_ms = process_threshold_ms
        # ``_running`` 标记运行器是否已进入工作状态。
        self._running = False
        # ``_scheduler`` 是 APScheduler 实例，用于定时选股与保存状态。
        self._scheduler = None
        # ``_scheduler_thread`` 是调度器所在线程。
        self._scheduler_thread = None
        # ``_heartbeat_callback`` 用于向看门狗报告主循环活跃状态。
        self._heartbeat_callback = None
        # ``_alert_callback`` 用于发送启动前账户校验告警。
        self._alert_callback = None

    def set_heartbeat_callback(self, callback) -> None:
        """注册心跳回调，供看门狗感知策略主循环是否仍在工作。"""
        self._heartbeat_callback = callback

    def set_alert_callback(self, callback) -> None:
        """注册预检查告警回调。

        当前主要用于把启动前的账户校验结果转发到钉钉。
        """
        self._alert_callback = callback

    # ------------------------------------------------------------------ 启动/停止

    def start(self) -> None:
        """启动策略运行器。"""
        self._running = True
        logger.info("StrategyRunner: 启动")

        # 尝试恢复状态
        if not self._load_state():
            # 无保存状态，从选股开始
            self.run_stock_selection()

        # 在真正开始盯盘前，先核对账户资产和账户持仓，
        # 防止策略内部状态与真实账户状态明显不一致。
        self._validate_account_constraints()

        # 订阅行情
        self._subscribe_all()

        # 注册数据回调
        if self._data_sub:
            self._data_sub.set_data_callback(self.on_market_data)

        # 启动调度器
        self._start_scheduler()

        # 仅在交易日激活策略
        self._activate_for_trading_day(reason="startup")

        logger.info("StrategyRunner: 已启动 %d 个策略", len(self._strategies))

    def stop(self) -> None:
        """停止运行器，并保存当前策略状态。"""
        self._running = False
        self.save_state()
        with self._lock:
            for s in self._strategies:
                if s.status == StrategyStatus.RUNNING:
                    s.pause()
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
        logger.info("StrategyRunner: 已停止")

    # ------------------------------------------------------------------ 数据分发

    def on_market_data(self, tick_data: Dict[str, TickData]) -> None:
        """处理一批最新行情数据。

        Args:
            tick_data: 以证券代码为键的行情字典。
        """
        if not self._running:
            return
        try:
            if self._heartbeat_callback:
                self._heartbeat_callback("strategy_runner")

            # 先做统一的延迟检测，避免策略内部各自重复判断。
            for code, tick in tick_data.items():
                if tick.latency_ms > self._latency_threshold * 1000:
                    print(f"[WARNING] 数据延迟 {tick.latency_ms/1000:.1f}s > "
                          f"{self._latency_threshold}s [{code}]")

            with self._lock:
                strategies = list(self._strategies)

            for strategy in strategies:
                code = strategy.stock_code
                tick = tick_data.get(code)
                if not tick:
                    continue
                t0 = time.perf_counter()
                try:
                    strategy.process_tick(tick)
                except Exception as e:
                    logger.error("StrategyRunner: Strategy[%s] 处理异常: %s",
                                 strategy.strategy_id[:8], e, exc_info=True)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                if elapsed_ms > self._process_threshold_ms:
                    logger.warning(
                        "StrategyRunner: Strategy[%s] 处理耗时 %.1fms 超过阈值 %.1fms",
                        strategy.strategy_id[:8], elapsed_ms, self._process_threshold_ms
                    )
                else:
                    logger.debug("StrategyRunner: Strategy[%s] 耗时 %.1fms",
                                 strategy.strategy_id[:8], elapsed_ms)

            # 每轮行情结束后顺手清理已停止策略，
            # 可以避免策略列表持续膨胀。
            self._cleanup_stopped()

        except Exception as e:
            logger.error("StrategyRunner: on_market_data 异常: %s", e, exc_info=True)

    # ------------------------------------------------------------------ 策略管理

    def add_strategy(self, strategy: BaseStrategy) -> None:
        """向运行器中添加一个策略实例。"""
        with self._lock:
            exists = next(
                (
                    s for s in self._strategies
                    if s.strategy_name == strategy.strategy_name
                    and s.stock_code == strategy.stock_code
                    and s.status != StrategyStatus.STOPPED
                ),
                None,
            )
            if exists:
                logger.info(
                    "StrategyRunner: 跳过重复策略 %s stock=%s",
                    strategy.strategy_name,
                    strategy.stock_code,
                )
                return

            self._strategies.append(strategy)

        if self._running and self.is_trading_day() and strategy.status == StrategyStatus.INITIALIZING:
            strategy.start()

        logger.info("StrategyRunner: 添加策略 %s stock=%s",
                    strategy.strategy_name, strategy.stock_code)
        with self._lock:
            should_subscribe = self._running and self.is_trading_day()

        # 订阅该标的
        if self._data_sub and should_subscribe:
            self._data_sub.subscribe_stocks([strategy.stock_code])

    def remove_strategy(self, strategy_id: str) -> None:
        """按策略 ID 移除策略实例。"""
        with self._lock:
            self._strategies = [s for s in self._strategies
                                 if s.strategy_id != strategy_id]
        logger.info("StrategyRunner: 移除策略 %s", strategy_id[:8])

    def get_strategy(self, strategy_id: str) -> Optional[BaseStrategy]:
        """按策略 ID 获取策略对象。"""
        with self._lock:
            for s in self._strategies:
                if s.strategy_id == strategy_id:
                    return s
        return None

    def get_all_strategies(self) -> List[BaseStrategy]:
        """返回当前全部策略对象的副本列表。"""
        with self._lock:
            return list(self._strategies)

    # ------------------------------------------------------------------ 选股

    def run_stock_selection(self) -> None:
        """执行选股，并为每个配置创建一个策略实例。"""
        if not self.is_trading_day():
            logger.info("StrategyRunner: 今日非交易日，跳过选股")
            return

        for cls in self._strategy_classes:
            try:
                configs: List[StrategyConfig] = []
                try:
                    with ProcessPoolExecutor(max_workers=1) as pool:
                        configs = pool.submit(_select_configs_in_subprocess, cls).result(timeout=30)
                except Exception as e:
                    logger.warning("StrategyRunner: 子进程选股失败，降级为主进程执行 [%s]: %s",
                                   cls.__name__, e)
                    configs = cls(
                        StrategyConfig(),
                        self._trade_exec,
                        self._position_mgr
                    ).select_stocks()

                for cfg in configs:
                    strategy = cls(cfg, self._trade_exec, self._position_mgr)
                    self.add_strategy(strategy)

            except Exception as e:
                logger.error("StrategyRunner: 选股异常 [%s]: %s",
                             cls.__name__, e, exc_info=True)

        self._activate_for_trading_day(reason="stock_selection")

    # ------------------------------------------------------------------ 持久化

    def save_state(self) -> None:
        """保存全部策略的快照状态。"""
        if not self._data_mgr:
            return
        try:
            with self._lock:
                snapshots = [s.get_snapshot() for s in self._strategies]
            self._data_mgr.save_strategy_state(snapshots)
        except Exception as e:
            logger.error("StrategyRunner: 保存状态失败: %s", e, exc_info=True)

    def _load_state(self) -> bool:
        """加载历史策略状态。

        Returns:
            是否成功恢复出至少一个策略实例。
        """
        if not self._data_mgr:
            return False
        snapshots = self._data_mgr.load_strategy_state(
            fallback_previous_market_day=self._load_previous_state_on_start,
        )
        if not snapshots:
            return False
        with self._lock:
            self._strategies.clear()
        for snap in snapshots:
            cls = self._find_strategy_class(snap.strategy_name)
            if not cls:
                logger.warning("StrategyRunner: 未找到策略类 %s，跳过恢复",
                               snap.strategy_name)
                continue
            strategy = cls(snap.config, self._trade_exec, self._position_mgr)
            strategy.restore_from_snapshot(snap)
            with self._lock:
                self._strategies.append(strategy)
        logger.info("StrategyRunner: 从快照恢复 %d 个策略", len(self._strategies))
        return len(self._strategies) > 0

    def _find_strategy_class(self, strategy_name: str) -> Optional[Type[BaseStrategy]]:
        """根据策略名称找到对应的策略类。"""
        for cls in self._strategy_classes:
            if cls.strategy_name == strategy_name:
                return cls
        return None

    # ------------------------------------------------------------------ 调度器

    def _start_scheduler(self) -> None:
        """启动 APScheduler 定时任务线程。"""
        try:
            from apscheduler.schedulers.blocking import BlockingScheduler
            from apscheduler.executors.pool import ProcessPoolExecutor as APSProcessPoolExecutor

            executors = {
                "default": {"type": "threadpool", "max_workers": 10},
                "processpool": APSProcessPoolExecutor(max_workers=2),
            }
            self._scheduler = BlockingScheduler(executors=executors)
            # 开盘前刷新当日策略并激活
            self._scheduler.add_job(self.run_stock_selection, "cron",
                                    hour=9, minute=25, id="stock_selection")
            # 收盘后保存状态
            self._scheduler.add_job(self.save_state, "cron",
                                    hour=15, minute=5, id="save_state")
            # 每30分钟清理已停止策略
            self._scheduler.add_job(self._cleanup_stopped, "interval",
                                    minutes=30, id="cleanup")
            self._scheduler_thread = threading.Thread(
                target=self._scheduler.start,
                daemon=True,
                name="strategy-scheduler"
            )
            self._scheduler_thread.start()
            logger.info("StrategyRunner: APScheduler 已启动")
        except ImportError:
            logger.warning("StrategyRunner: apscheduler 未安装，跳过定时任务")
        except Exception as e:
            logger.error("StrategyRunner: 调度器启动失败: %s", e, exc_info=True)

    def is_trading_time(self) -> bool:
        """判断当前是否位于日内交易时段。"""
        now = datetime.now()
        if not self.is_trading_day(now):
            return False
        t = now.strftime("%H:%M")
        return (("09:30" <= t <= "11:30") or ("13:00" <= t <= "15:00"))

    def is_trading_day(self, when=None) -> bool:
        """判断指定日期是否为交易日。"""
        target = when or datetime.now()
        return is_market_day(target)

    def _activate_for_trading_day(self, reason: str = "") -> bool:
        """在交易日激活策略、恢复订阅。"""
        if not self.is_trading_day():
            logger.info("StrategyRunner: 今日非交易日，跳过策略激活 [%s]", reason or "unknown")
            return False

        self._subscribe_all()

        started = 0
        with self._lock:
            for strategy in self._strategies:
                if strategy.status == StrategyStatus.INITIALIZING:
                    strategy.start()
                    started += 1

        logger.info("StrategyRunner: 交易日激活完成 [%s]，新增启动 %d 个策略",
                    reason or "unknown", started)
        return True

    def _subscribe_all(self) -> None:
        """订阅当前所有策略涉及的证券代码。"""
        if not self._data_sub:
            return
        with self._lock:
            # 用集合去重，避免多个策略订阅同一标的时重复请求。
            codes = list({s.stock_code for s in self._strategies})
        if codes:
            self._data_sub.subscribe_stocks(codes)

    def _cleanup_stopped(self) -> None:
        """移除已停止且无持仓的策略，同时归档盈亏"""
        removed_ids = []
        with self._lock:
            remaining = []
            for strategy in self._strategies:
                if strategy.status == StrategyStatus.STOPPED:
                    removed_ids.append(strategy.strategy_id)
                else:
                    remaining.append(strategy)
            self._strategies = remaining

        if removed_ids and self._position_mgr:
            for strategy_id in removed_ids:
                try:
                    self._position_mgr.remove_position(strategy_id)
                except Exception as e:
                    logger.error("StrategyRunner: 清理策略持仓失败 [%s]: %s",
                                 strategy_id[:8], e, exc_info=True)

        removed = len(removed_ids)
        if removed:
            logger.info("StrategyRunner: 清理并归档 %d 个已停止策略", removed)

    def dispatch_order_update(self, order) -> None:
        """将订单更新分发给对应策略"""
        strategy = self.get_strategy(order.strategy_id)
        if strategy:
            strategy.on_order_update(order)

    def _validate_account_constraints(self) -> None:
        """在策略运行前核对账户资产和账户持仓。

        校验规则：
        1. 策略的最大可用资金不能明显大于账户可用资金。
        2. 策略内部记录的标的持仓数量不能大于账户真实持仓数量。
        3. 策略内部记录的可用数量不能大于账户真实可用数量。

        注意：这里按用户要求仅发出警告，不阻止程序继续运行。
        """
        if not self._connection_mgr or not self._connection_mgr.is_connected():
            logger.info("StrategyRunner: 启动前账户校验已跳过，交易连接未就绪")
            return

        account_asset = self._connection_mgr.query_stock_asset()
        account_positions = self._connection_mgr.query_stock_positions()

        if account_asset is None:
            self._warn_preflight("[启动前校验] 无法获取账户资产信息，已跳过资金上限核验")
            return

        available_cash = float(getattr(account_asset, "cash", 0.0) or 0.0)
        total_asset = float(getattr(account_asset, "total_asset", 0.0) or 0.0)

        with self._lock:
            strategies = list(self._strategies)

        for strategy in strategies:
            class_budget_limit = float(getattr(strategy, "max_total_amount", 0.0) or 0.0)
            config_budget_limit = float(getattr(strategy.config, "max_position_amount", 0.0) or 0.0)

            if class_budget_limit > 0 and class_budget_limit > available_cash:
                self._warn_preflight(
                    f"[启动前校验] 策略 {strategy.strategy_name}[{strategy.strategy_id[:8]}] "
                    f"类级最大资金 {class_budget_limit:.2f} 超过账户可用资金 {available_cash:.2f} "
                    f"(总资产 {total_asset:.2f})"
                )

            if config_budget_limit > 0 and config_budget_limit > available_cash:
                self._warn_preflight(
                    f"[启动前校验] 策略 {strategy.strategy_name}[{strategy.strategy_id[:8]}] "
                    f"标的最大资金 {config_budget_limit:.2f} 超过账户可用资金 {available_cash:.2f} "
                    f"(标的 {strategy.stock_code})"
                )

        if not self._position_mgr:
            return

        strategy_position_map: Dict[str, Dict[str, object]] = {}
        for position in self._position_mgr.get_all_positions().values():
            info = strategy_position_map.setdefault(position.stock_code, {
                "total_quantity": 0,
                "available_quantity": 0,
                "strategy_names": set(),
            })
            info["total_quantity"] = int(info["total_quantity"]) + int(position.total_quantity or 0)
            info["available_quantity"] = int(info["available_quantity"]) + int(position.available_quantity or 0)
            cast_names = info["strategy_names"]
            if isinstance(cast_names, set):
                cast_names.add(position.strategy_name)

        account_position_map: Dict[str, Dict[str, int]] = {}
        for account_position in account_positions:
            code = self._xt_to_code(str(getattr(account_position, "stock_code", "") or ""))
            account_position_map[code] = {
                "volume": int(getattr(account_position, "volume", 0) or 0),
                "can_use_volume": int(getattr(account_position, "can_use_volume", 0) or 0),
            }

        for stock_code, info in strategy_position_map.items():
            strategy_total = int(info.get("total_quantity", 0) or 0)
            strategy_available = int(info.get("available_quantity", 0) or 0)
            strategy_names = ",".join(sorted(info.get("strategy_names", set()) or []))
            account_position = account_position_map.get(stock_code)

            if strategy_total <= 0 and strategy_available <= 0:
                continue

            if not account_position:
                self._warn_preflight(
                    f"[启动前校验] 策略持仓显示 {stock_code} 共 {strategy_total} 股，"
                    f"但账户中未查询到该标的持仓（策略: {strategy_names or '-' }）"
                )
                continue

            if strategy_total > int(account_position.get("volume", 0) or 0):
                self._warn_preflight(
                    f"[启动前校验] 策略持仓 {stock_code} 共 {strategy_total} 股，"
                    f"超过账户实际持仓 {account_position['volume']} 股（策略: {strategy_names or '-' }）"
                )

            if strategy_available > int(account_position.get("can_use_volume", 0) or 0):
                self._warn_preflight(
                    f"[启动前校验] 策略可用持仓 {stock_code} 共 {strategy_available} 股，"
                    f"超过账户实际可用持仓 {account_position['can_use_volume']} 股（策略: {strategy_names or '-' }）"
                )

    def _warn_preflight(self, message: str) -> None:
        """统一处理启动前校验警告：同时写日志并发送告警。"""
        logger.warning(message)
        if self._alert_callback:
            try:
                self._alert_callback(AlertLevel.WARNING, message)
            except Exception as exc:
                logger.error("StrategyRunner: 启动前告警发送失败: %s", exc, exc_info=True)

    @staticmethod
    def _xt_to_code(xt_code: str) -> str:
        """把 xtquant 证券代码转换为 6 位内部证券代码。"""
        return xt_code.split(".")[0] if "." in xt_code else xt_code


__all__ = ["StrategyRunner"]
