"""策略基类模块。

本模块定义所有策略必须遵循的统一接口与通用行为，包括：
- 行情处理入口
- 信号到交易动作的转换
- 通用止损止盈检查
- 订单跟踪与快照恢复

这样不同策略只需要关注“何时买卖”，而不必重复实现公共骨架。
"""
import uuid
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional

from config.enums import StrategyStatus
from core.models import TickData
from strategy.models import StrategyConfig, StrategySnapshot
from trading.models import Order
from monitor.logger import get_logger

logger = get_logger("trade")


class BaseStrategy(ABC):
    """策略基类模板。

    类属性（所有策略实例共享）需在子类中定义：
        strategy_name: str      = "未命名策略"
        max_positions: int      = 1       最大持仓标的数
        max_total_amount: float = 0.0     最大可用总金额

    对象属性（每个实例独立）：
        strategy_id:       str (UUID)
        stock_code:        str
        status:            StrategyStatus
        config:            StrategyConfig
    """

    # ---- 子类需覆盖的类属性 -------------------------------------------------
    strategy_name: str = "BaseStrategy"
    max_positions: int = 5
    max_total_amount: float = 100000.0

    # ---- 类级别共享统计（需子类自行维护 thread safety 如有需要） ----
    current_positions: int = 0
    current_used_amount: float = 0.0
    _class_used_amount: float = 0.0
    _current_positions_count: int = 0  # 当前持仓标的数
    _lock = threading.Lock()  # 类级别锁，保护共享统计

    def __init__(self, config: StrategyConfig,
                 trade_executor=None, position_manager=None):
        """初始化策略实例。

        Args:
            config: 当前策略实例的配置对象。
            trade_executor: 交易执行器。
            position_manager: 持仓管理器。
        """
        # ``strategy_id`` 是每个策略实例的唯一标识。
        self.strategy_id: str = str(uuid.uuid4())
        # ``stock_code`` 表示当前策略实例唯一负责的标的代码。
        self.stock_code: str = config.stock_code
        # ``status`` 记录策略当前运行状态，例如运行中、暂停、已停止。
        self.status: StrategyStatus = StrategyStatus.INITIALIZING
        # ``config`` 保存该策略实例的全部配置参数。
        self.config: StrategyConfig = config
        # ``_trade_executor`` 负责把策略信号变成下单请求。
        self._trade_executor = trade_executor
        # ``_position_mgr`` 用于查询当前策略的持仓状态。
        self._position_mgr = position_manager
        # ``_pending_orders`` 保存尚未终结的订单对象，便于防重复下单和状态跟踪。
        self._pending_orders: Dict[str, Order] = {}   # {order_uuid: Order}
        # ``_create_time`` 记录该策略实例创建时间。
        self._create_time = datetime.now()
        # ``_orders_history`` 保存该策略实例经历过的全部订单历史。
        self._orders_history: List[Order] = []

        logger.info("Strategy[%s] %s 初始化 stock=%s",
                    self.strategy_id[:8], self.strategy_name, self.stock_code)

    # ------------------------------------------------------------------ Abstract

    @abstractmethod
    def on_tick(self, tick: TickData) -> Optional[dict]:
        """
        每次行情推送时调用。
        只生成信号，不直接调用交易方法（信号与交易分离）。

        Returns:
            信号字典 或 None（无信号）
            格式: {
                "action": "BUY" | "SELL" | "CLOSE",
                "price": float,
                "quantity": int,       # 按数量时使用
                "amount": float,       # 按金额时使用
                "remark": str,
            }
        """

    @abstractmethod
    def select_stocks(self) -> List[StrategyConfig]:
        """
        选股方法。返回待开仓标的的配置列表。
        可从外部文件读取，也可自行计算。
        """

    # ------------------------------------------------------------------ 主处理流程

    def process_tick(self, tick: TickData) -> None:
        """处理一条最新行情。

        Args:
            tick: 当前标的的最新标准化行情对象。
        """
        if self.status not in (StrategyStatus.RUNNING,):
            return
        if tick.stock_code != self.stock_code:
            return
        try:
            # 更新持仓最新价
            if self._position_mgr:
                self._position_mgr.update_price(self.stock_code, tick.last_price)

            # 风控前置检查
            if self._check_risk(tick):
                return

            # 先由子类根据行情生成信号，
            # 再统一走 `_execute_signal()` 转换成交易动作。
            signal = self.on_tick(tick)

            # 执行信号
            if signal:
                self._execute_signal(signal)

        except Exception as e:
            logger.error("Strategy[%s] process_tick 异常: %s",
                         self.strategy_id[:8], e, exc_info=True)
            self.status = StrategyStatus.ERROR

    def _check_risk(self, tick: TickData) -> bool:
        """执行通用风控检查。

        Returns:
            若已触发止损或止盈并完成处理，则返回 `True`。
        """
        if self.check_stop_loss(tick):
            logger.warning("Strategy[%s] 触发止损 price=%.3f stop=%.3f",
                           self.strategy_id[:8], tick.last_price,
                           self.config.stop_loss_price)
            self.close_position(remark="触发止损")
            return True
        if self.check_take_profit(tick):
            logger.info("Strategy[%s] 触发止盈 price=%.3f tp=%.3f",
                        self.strategy_id[:8], tick.last_price,
                        self.config.take_profit_price)
            self.close_position(remark="触发止盈")
            return True
        return False

    def _execute_signal(self, signal: dict) -> None:
        """根据信号执行交易。

        策略只负责生成信号；真正的下单动作统一走交易执行器，
        这样可以让“策略逻辑”和“交易接口细节”彻底分离。
        """
        action = signal.get("action", "").upper()
        price = float(signal.get("price", 0))
        quantity = int(signal.get("quantity", 0))
        amount = float(signal.get("amount", 0))
        remark = signal.get("remark", action)

        if action == "BUY":
            if amount > 0:
                self.add_position_by_amount(price, amount, remark)
            elif quantity > 0:
                self.add_position(price, quantity, remark)
        elif action == "SELL":
            if quantity > 0:
                self.reduce_position(price, quantity, remark)
        elif action == "CLOSE":
            self.close_position(remark)

    # ------------------------------------------------------------------ 仓位操作

    def add_position(self, price: float, quantity: int, remark: str = "") -> Optional[Order]:
        """按股数加仓，并执行最大持仓金额限制检查。"""
        if not self._trade_executor:
            return None
            
        # 这里的风控口径是“当前持仓市值 + 本次计划委托金额”。
        if self._position_mgr and self.config.max_position_amount > 0:
            pos = self._position_mgr.get_position(self.strategy_id)
            current_value = pos.market_value if pos else 0.0
            order_value = price * quantity
            if current_value + order_value > self.config.max_position_amount:
                logger.warning("Strategy[%s] 加仓受限: 当前市值 %.2f + 购买金额 %.2f > 上限 %.2f",
                               self.strategy_id[:8], current_value, order_value, self.config.max_position_amount)
                # 调整为允许的最大可买数量
                allowed_amount = self.config.max_position_amount - current_value
                if allowed_amount < price * 100:  # 不足一手
                    return None
                quantity = int((allowed_amount / price) // 100) * 100
                logger.info("Strategy[%s] 订单重置为允许的最大数量: %d 股", self.strategy_id[:8], quantity)

        order = self._trade_executor.buy_limit(
            self.strategy_id, self.strategy_name,
            self.stock_code, price, quantity, remark
        )
        self._track_order(order)
        # 订单成交后会通过成交回调更新持仓，此处同步类属性
        self.__class__._sync_class_stats(self._position_mgr)
        return order

    def add_position_by_amount(self, price: float, amount: float,
                                remark: str = "") -> Optional[Order]:
        """按金额加仓，并执行最大持仓金额限制检查。"""
        if not self._trade_executor:
            return None
            
        # 仓位上限风控
        if self._position_mgr and self.config.max_position_amount > 0:
            pos = self._position_mgr.get_position(self.strategy_id)
            current_value = pos.market_value if pos else 0.0
            if current_value + amount > self.config.max_position_amount:
                logger.warning("Strategy[%s] 加仓金额受限: 当前市值 %.2f + 计划金额 %.2f > 上限 %.2f",
                               self.strategy_id[:8], current_value, amount, self.config.max_position_amount)
                amount = self.config.max_position_amount - current_value
                if amount < price * 100:
                    return None

        order = self._trade_executor.buy_by_amount(
            self.strategy_id, self.strategy_name,
            self.stock_code, price, amount, remark
        )
        self._track_order(order)
        return order

    def reduce_position(self, price: float, quantity: int,
                        remark: str = "") -> Optional[Order]:
        """按指定价格和数量减仓。"""
        if not self._trade_executor:
            return None
        order = self._trade_executor.sell_limit(
            self.strategy_id, self.strategy_name,
            self.stock_code, price, quantity, remark
        )
        self._track_order(order)
        return order

    def close_position(self, remark: str = "") -> Optional[Order]:
        """提交清仓请求。"""
        if not self._trade_executor:
            return None
        order = self._trade_executor.close_position(
            self.strategy_id, self.strategy_name,
            self.stock_code, remark=remark or "策略平仓"
        )
        self._track_order(order)
        # 平仓成交后同步类属性
        self.__class__._sync_class_stats(self._position_mgr)
        return order

    # ------------------------------------------------------------------ 止盈止损

    def check_stop_loss(self, tick: TickData) -> bool:
        """止损检查（子类可覆盖）"""
        if self.config.stop_loss_price <= 0:
            return False
        pos = self._position_mgr.get_position(self.strategy_id) if self._position_mgr else None
        if not pos or pos.total_quantity <= 0:
            return False
        return tick.last_price <= self.config.stop_loss_price

    def check_take_profit(self, tick: TickData) -> bool:
        """止盈检查（子类可覆盖）"""
        if self.config.take_profit_price <= 0:
            return False
        pos = self._position_mgr.get_position(self.strategy_id) if self._position_mgr else None
        if not pos or pos.total_quantity <= 0:
            return False
        return tick.last_price >= self.config.take_profit_price

    # ------------------------------------------------------------------ 控制

    def start(self) -> None:
        """把策略状态切换为运行中。"""
        self.status = StrategyStatus.RUNNING
        logger.info("Strategy[%s] %s 启动", self.strategy_id[:8], self.strategy_name)

    def pause(self) -> None:
        """把策略状态切换为暂停。"""
        self.status = StrategyStatus.PAUSED
        logger.info("Strategy[%s] 暂停", self.strategy_id[:8])

    def resume(self) -> None:
        """恢复策略运行。"""
        self.status = StrategyStatus.RUNNING
        logger.info("Strategy[%s] 恢复", self.strategy_id[:8])

    def stop(self) -> None:
        """停止策略运行。"""
        self.status = StrategyStatus.STOPPED
        logger.info("Strategy[%s] 停止", self.strategy_id[:8])

    # ------------------------------------------------------------------ 订单回调

    def on_order_update(self, order: Order) -> None:
        """处理订单状态更新回调。

        Args:
            order: 最新状态的订单对象。
        """
        try:
            if order.strategy_id != self.strategy_id:
                return
            from config.enums import OrderStatus, OrderDirection
            # 订单进入终态后，从待处理列表中移除。
            if order.order_uuid in self._pending_orders:
                if order.status in (OrderStatus.SUCCEEDED, OrderStatus.CANCELED,
                                    OrderStatus.PART_CANCEL, OrderStatus.JUNK,
                                    OrderStatus.UNKNOWN):
                    self._pending_orders.pop(order.order_uuid, None)

                # 卖出订单全部成交后，如果该策略已经没有持仓，
                # 则自动将策略标记为停止状态。
            if (order.direction == OrderDirection.SELL and
                    order.status == OrderStatus.SUCCEEDED and
                    self._position_mgr):
                pos = self._position_mgr.get_position(self.strategy_id)
                if not pos or pos.total_quantity <= 0:
                    self.stop()

            self.__class__._sync_class_stats(self._position_mgr)
            self._on_order_update_hook(order)
        except Exception as e:
            logger.error("Strategy[%s] on_order_update 异常: %s",
                         self.strategy_id[:8], e, exc_info=True)

    def _on_order_update_hook(self, order: Order) -> None:
        """子类可覆盖以处理订单状态变更"""
        pass

    # ------------------------------------------------------------------ 持久化

    def get_snapshot(self) -> StrategySnapshot:
        """生成当前策略的可持久化快照。"""
        pos = None
        if self._position_mgr:
            pos = self._position_mgr.get_position(self.strategy_id)

        from position.models import PositionInfo
        return StrategySnapshot(
            strategy_id=self.strategy_id,
            strategy_name=self.strategy_name,
            stock_code=self.stock_code,
            status=self.status,
            config=self.config,
            position=pos or PositionInfo(),
            pending_order_uuids=list(self._pending_orders.keys()),
            custom_state=self._get_custom_state(),
            update_time=datetime.now(),
        )

    def restore_from_snapshot(self, snapshot: StrategySnapshot) -> None:
        """从历史快照恢复策略状态。"""
        self.strategy_id = snapshot.strategy_id
        self.stock_code = snapshot.stock_code
        self.status = snapshot.status
        self.config = snapshot.config
        
        # 持仓恢复与策略对象恢复分开进行，
        # 这样持仓管理器仍然是唯一的持仓状态维护中心。
        if self._position_mgr and snapshot.position:
            # 使用 restore_position 方法，内部自动处理加锁和 T+1 解锁
            self._position_mgr.restore_position(self.strategy_id, snapshot.position)
            self.__class__._sync_class_stats(self._position_mgr)
            
        self._restore_custom_state(snapshot.custom_state)
        logger.info("Strategy[%s] 从快照恢复 stock=%s status=%s",
                    self.strategy_id[:8], self.stock_code, self.status.value)

    def _get_custom_state(self) -> dict:
        """子类覆盖，返回需持久化的额外状态"""
        return {}

    def _restore_custom_state(self, state: dict) -> None:
        """子类覆盖，恢复额外状态"""
        pass

    # ------------------------------------------------------------------ Private

    def _track_order(self, order: Order) -> None:
        """把订单记录到待处理列表和历史列表。"""
        if order and order.is_active():
            self._pending_orders[order.order_uuid] = order
        self._orders_history.append(order)

    @classmethod
    def _sync_class_stats(cls, position_manager=None) -> None:
        """同步类级别统计信息。

        这些统计量是“按策略类聚合”的，而不是按单个实例聚合，
        便于子类实现全局仓位数和总资金占用约束。
        """
        if not position_manager:
            return
        try:
            with cls._lock:
                positions = position_manager.get_all_positions()
                # 统计该策略类的所有实例的持仓
                class_positions = [p for p in positions.values() 
                                 if p.strategy_name == cls.strategy_name]
                cls._current_positions_count = len(class_positions)
                cls._class_used_amount = sum(p.market_value for p in class_positions)
                cls.current_positions = cls._current_positions_count
                cls.current_used_amount = cls._class_used_amount
                logger.debug(f"Strategy[{cls.strategy_name}] 类属性同步: "
                           f"当前持仓数={cls._current_positions_count}, "
                           f"已用金额={cls._class_used_amount:.2f}")
        except Exception as e:
            logger.warning(f"Strategy[{cls.strategy_name}] 同步类属性失败: {e}")

    def __repr__(self) -> str:
        """返回便于调试的对象描述字符串。"""
        return (f"<{self.strategy_name}[{self.strategy_id[:8]}] "
                f"stock={self.stock_code} status={self.status.value}>")


__all__ = ["BaseStrategy"]
