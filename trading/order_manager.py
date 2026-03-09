"""
订单管理模块
- 追踪每笔订单的全生命周期
- 接收成交回报并通知持仓/策略模块（事件回调）
- 持久化到 SQLite（通过 DataManager）
"""
import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional

from trading.models import Order, TradeRecord
from config.enums import OrderStatus, OrderDirection
from monitor.logger import get_logger

logger = get_logger("trade")


class OrderManager:
    """订单追踪管理器"""

    def __init__(self, data_manager=None, fee_schedule=None):
        self._data_mgr = data_manager
        self._fee_schedule = fee_schedule
        self._orders: Dict[str, Order] = {}                  # {order_uuid: Order}
        self._xt_to_uuid: Dict[int, str] = {}               # {xt_order_id: order_uuid}
        self._seq_to_uuid: Dict[int, str] = {}              # {async_seq: order_uuid} 临时映射
        self._position_callback: Optional[Callable[[TradeRecord], None]] = None
        self._strategy_callback: Optional[Callable[[Order], None]] = None
        self._trade_callback: Optional[Callable[[TradeRecord], None]] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 注册

    def register_order(self, order: Order) -> None:
        """注册新订单"""
        with self._lock:
            self._orders[order.order_uuid] = order
            if order.xt_order_id:
                self._xt_to_uuid[order.xt_order_id] = order.order_uuid
        if self._data_mgr:
            try:
                self._data_mgr.save_order(order)
            except Exception as e:
                logger.error("OrderManager: 持久化订单失败: %s", e, exc_info=True)
        logger.info("[ORDER] 注册订单 uuid=%s%s code=%s dir=%s price=%.3f qty=%d remark=%s",
                    order.order_uuid[:8], f" xt_id={order.xt_order_id}" if order.xt_order_id else "",
                    order.stock_code, order.direction.value,
                    order.price, order.quantity, order.remark)

    # ------------------------------------------------------------------ 状态更新

    def update_order_status(self, xt_order_id: int, status: OrderStatus,
                             filled_qty: int = 0, filled_amount: float = 0,
                             avg_price: float = 0) -> None:
        """更新订单状态（由 callback 调用）"""
        with self._lock:
            uuid = self._xt_to_uuid.get(xt_order_id)
            if not uuid:
                return
            order = self._orders.get(uuid)
            if not order:
                return
            order.status = status
            if filled_qty:
                order.filled_quantity = filled_qty
            if filled_amount:
                order.filled_amount = filled_amount
            if avg_price:
                order.filled_avg_price = avg_price
            order.update_time = datetime.now()

        if self._data_mgr:
            try:
                self._data_mgr.save_order(order)
            except Exception as e:
                logger.error("OrderManager: 更新持久化失败: %s", e, exc_info=True)

        if self._strategy_callback:
            try:
                self._strategy_callback(order)
            except Exception as e:
                logger.error("OrderManager: 策略回调异常: %s", e, exc_info=True)

        logger.debug("[ORDER] 订单状态变更 uuid=%s status=%s", uuid[:8], status.value)

    def on_trade(self, xt_order_id: int, trade_info: dict) -> None:
        """成交回报入口"""
        try:
            with self._lock:
                uuid = self._xt_to_uuid.get(xt_order_id)
                order = self._orders.get(uuid) if uuid else None

            strategy_id = order.strategy_id if order else ""
            strategy_name = str(trade_info.get("strategy_name", "") or "") or (order.strategy_name if order else "")

            xt_order_type = self._to_int(trade_info.get("order_type", 0))
            xt_direction = self._to_int(trade_info.get("direction", 0))
            offset_flag = self._to_int(trade_info.get("offset_flag", 0))
            xt_traded_time = self._to_int(trade_info.get("traded_time", 0))
            traded_at = self._parse_xt_traded_time(xt_traded_time)
            direction = self._infer_trade_direction(
                offset_flag=offset_flag,
                order_type=xt_order_type,
                xt_direction=xt_direction,
                fallback_order=order,
                raw_direction=trade_info.get("direction", ""),
            )

            trade = TradeRecord(
                account_type=int(trade_info.get("account_type", 0) or 0),
                account_id=str(trade_info.get("account_id", "") or ""),
                order_type=xt_order_type,
                trade_id=str(trade_info.get("traded_id", trade_info.get("trade_id", "")) or ""),
                xt_traded_time=xt_traded_time,
                order_uuid=uuid or "",
                xt_order_id=xt_order_id,
                order_sysid=str(trade_info.get("order_sysid", "") or ""),
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                order_remark=str(trade_info.get("order_remark", "") or ""),
                stock_code=str(trade_info.get("stock_code", "") or ""),
                direction=direction,
                xt_direction=xt_direction,
                offset_flag=offset_flag,
                price=float(trade_info.get("traded_price", trade_info.get("price", 0)) or 0),
                quantity=int(trade_info.get("traded_volume", trade_info.get("quantity", 0)) or 0),
                amount=float(trade_info.get("traded_amount", trade_info.get("amount", 0)) or 0),
                commission=float(trade_info.get("commission", 0)),
                trade_time=traded_at,
            )
            self._apply_fee_breakdown(trade)

            # 更新订单已成交量
            if order:
                with self._lock:
                    order.filled_quantity += trade.quantity
                    order.filled_amount += trade.amount
                    order.commission += trade.total_fee
                    if order.filled_quantity > 0:
                        order.filled_avg_price = order.filled_amount / order.filled_quantity
                    if order.filled_quantity >= order.quantity:
                        order.status = OrderStatus.SUCCEEDED
                    else:
                        order.status = OrderStatus.PART_SUCC
                    order.update_time = datetime.now()

            # 持久化成交
            if self._data_mgr:
                try:
                    self._data_mgr.save_trade(trade)
                    if order:
                        self._data_mgr.save_order(order)
                except Exception as e:
                    logger.error("OrderManager: 成交持久化失败: %s", e, exc_info=True)

            # 通知持仓模块
            if self._position_callback:
                try:
                    self._position_callback(trade)
                except Exception as e:
                    logger.error("OrderManager: 持仓回调异常: %s", e, exc_info=True)

            # 通知成交监听方（如 WebSocket）
            if self._trade_callback:
                try:
                    self._trade_callback(trade)
                except Exception as e:
                    logger.error("OrderManager: 成交通知回调异常: %s", e, exc_info=True)

            # 通知策略模块
            if order and self._strategy_callback:
                try:
                    self._strategy_callback(order)
                except Exception as e:
                    logger.error("OrderManager: 策略回调异常: %s", e, exc_info=True)

            logger.info("[ORDER] [TRADE] 成交 uuid=%s code=%s price=%.3f qty=%d",
                        (uuid or "?")[:8], trade.stock_code, trade.price, trade.quantity)

        except Exception as e:
            logger.error("OrderManager: on_trade 处理异常: %s", e, exc_info=True)

    @staticmethod
    def _infer_trade_direction(offset_flag: int, order_type: int, xt_direction: int,
                               fallback_order: Optional[Order], raw_direction) -> OrderDirection:
        """根据 XtTrade 字段推断买卖方向（优先 offset_flag/order_type）。"""
        buy_markers = {23}
        sell_markers = {24}

        for marker in (offset_flag, order_type, xt_direction):
            if marker in buy_markers:
                return OrderDirection.BUY
            if marker in sell_markers:
                return OrderDirection.SELL

        raw = str(raw_direction or "").upper()
        if "BUY" in raw:
            return OrderDirection.BUY
        if "SELL" in raw:
            return OrderDirection.SELL

        if fallback_order:
            return fallback_order.direction
        return OrderDirection.BUY

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_xt_traded_time(xt_traded_time: int) -> datetime:
        if xt_traded_time <= 0:
            return datetime.now()
        text = str(xt_traded_time)
        for fmt, length in (("%Y%m%d%H%M%S", 14), ("%Y%m%d", 8)):
            try:
                return datetime.strptime(text[:length], fmt)
            except ValueError:
                continue
        return datetime.now()

    def _apply_fee_breakdown(self, trade: TradeRecord) -> None:
        if trade.amount <= 0 and trade.price > 0 and trade.quantity > 0:
            trade.amount = trade.price * trade.quantity

        if self._fee_schedule:
            fee = self._fee_schedule.calculate(trade.stock_code, trade.direction, trade.amount)
            trade.buy_commission = fee.buy_commission
            trade.sell_commission = fee.sell_commission
            trade.stamp_tax = fee.stamp_tax
            trade.total_fee = fee.total_fee
            trade.is_t0 = fee.is_t0
            trade.commission = fee.total_fee
            return

        trade.total_fee = float(trade.commission or 0.0)
        if trade.direction == OrderDirection.BUY:
            trade.buy_commission = trade.total_fee
        else:
            trade.sell_commission = trade.total_fee

    def on_async_response(self, seq: int, xt_order_id: int) -> None:
        """绑定异步下单序列号与柜台订单号"""
        with self._lock:
            uuid = self._seq_to_uuid.pop(seq, None)
            if uuid:
                self._xt_to_uuid[xt_order_id] = uuid
                order = self._orders.get(uuid)
                if order:
                    order.xt_order_id = xt_order_id
                    order.status = OrderStatus.WAIT_REPORTING
                    order.update_time = datetime.now()
        logger.debug("OrderManager: async_response seq=%d → xt_id=%d", seq, xt_order_id)

    def register_seq(self, seq: int, order_uuid: str) -> None:
        """为异步下单注册 seq → uuid 映射"""
        with self._lock:
            self._seq_to_uuid[int(seq)] = order_uuid

    # ------------------------------------------------------------------ 查询

    def get_order(self, order_uuid: str) -> Optional[Order]:
        return self._orders.get(order_uuid)

    def get_order_by_xt_id(self, xt_order_id: int) -> Optional[Order]:
        with self._lock:
            uuid = self._xt_to_uuid.get(xt_order_id)
            return self._orders.get(uuid) if uuid else None

    def get_orders_by_strategy(self, strategy_id: str) -> List[Order]:
        with self._lock:
            return [o for o in self._orders.values() if o.strategy_id == strategy_id]

    def get_active_orders(self) -> List[Order]:
        with self._lock:
            return [o for o in self._orders.values() if o.is_active()]

    # ------------------------------------------------------------------ 回调注册

    def set_position_callback(self, callback: Callable[[TradeRecord], None]) -> None:
        self._position_callback = callback

    def set_strategy_callback(self, callback: Callable[[Order], None]) -> None:
        self._strategy_callback = callback

    def set_trade_callback(self, callback: Callable[[TradeRecord], None]) -> None:
        self._trade_callback = callback


__all__ = ["OrderManager"]
