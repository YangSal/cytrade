"""
策略运行模块
- 管理策略对象列表
- 数据分发给每个策略
- 状态持久化与跨交易日恢复
- APScheduler 定时任务（选股更新、状态保存等）
"""
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Type

from config.enums import StrategyStatus
from core.models import TickData
from core.trading_calendar import is_market_day
from strategy.base import BaseStrategy
from strategy.models import StrategyConfig, StrategySnapshot
from monitor.logger import get_logger

logger = get_logger("system")


def _select_configs_in_subprocess(strategy_class):
    """在子进程中执行选股，返回 ``StrategyConfig`` 列表。"""
    strategy = strategy_class(StrategyConfig(), None, None)
    return strategy.select_stocks()


class StrategyRunner:
    """策略运行管理器。

    它负责统一调度所有策略对象，是策略层的总控中心。
    """

    def __init__(self, data_subscription=None, trade_executor=None,
                 position_manager=None, data_manager=None,
                 strategy_classes: List[Type[BaseStrategy]] = None,
                 latency_threshold_sec: float = 10.0,
                 process_threshold_ms: float = 200.0):
        self._data_sub = data_subscription
        self._trade_exec = trade_executor
        self._position_mgr = position_manager
        self._data_mgr = data_manager
        self._strategy_classes = strategy_classes or []
        self._strategies: List[BaseStrategy] = []
        self._lock = threading.Lock()
        self._latency_threshold = latency_threshold_sec
        self._process_threshold_ms = process_threshold_ms
        self._running = False
        self._scheduler = None
        self._scheduler_thread = None
        self._heartbeat_callback = None

    def set_heartbeat_callback(self, callback) -> None:
        """注册心跳回调，供看门狗感知策略主循环是否仍在工作。"""
        self._heartbeat_callback = callback

    # ------------------------------------------------------------------ 启动/停止

    def start(self) -> None:
        """启动策略运行"""
        self._running = True
        logger.info("StrategyRunner: 启动")

        # 尝试恢复状态
        if not self._load_state():
            # 无保存状态，从选股开始
            self.run_stock_selection()

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
        """停止所有策略，保存状态"""
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
        """
        行情数据回调入口（由 DataSubscriptionManager 调用）
        1. 记录数据延迟
        2. 分发给每个策略
        3. 记录每个策略处理耗时
        """
        if not self._running:
            return
        try:
            if self._heartbeat_callback:
                self._heartbeat_callback("strategy_runner")

            # 打印数据延迟（终端）
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

            # 清理已结束的策略
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
        """执行选股，为每个选出标的创建策略对象"""
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
        """保存所有策略状态"""
        if not self._data_mgr:
            return
        try:
            with self._lock:
                snapshots = [s.get_snapshot() for s in self._strategies]
            self._data_mgr.save_strategy_state(snapshots)
        except Exception as e:
            logger.error("StrategyRunner: 保存状态失败: %s", e, exc_info=True)

    def _load_state(self) -> bool:
        """加载策略状态，返回是否成功"""
        if not self._data_mgr:
            return False
        snapshots = self._data_mgr.load_strategy_state()
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
        """判断当前是否在交易时间"""
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
        """订阅所有策略的标的"""
        if not self._data_sub:
            return
        with self._lock:
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


__all__ = ["StrategyRunner"]
