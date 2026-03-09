"""
交易执行模块
- 封装多种下单方式：限价/市价/按金额/按数量/平仓/撤单
- 每笔订单内部分配 UUID，同时追踪柜台订单号
- 每笔订单必须有 remark
- 下单后注册到 OrderManager
"""
import math
import time
from typing import Optional

from trading.models import Order
from trading.order_manager import OrderManager
from config.enums import OrderDirection, OrderType, OrderStatus
from monitor.logger import get_logger

logger = get_logger("trade")

try:
    from xtquant import xtconstant
    _XT_AVAILABLE = True
except ImportError:
    _XT_AVAILABLE = False

    class xtconstant:  # type: ignore
        STOCK_BUY = 23
        STOCK_SELL = 24
        FIX_PRICE = 11       # 限价
        MARKET_SH_INSTANT = 42   # 上海市价（最优五档即时成交）
        MARKET_SZ_CONVERT = 45   # 深圳市价（即时成交剩余转限价）


class TradeExecutor:
    """交易执行器。

    该模块负责把策略发出的“买/卖/平仓/撤单意图”翻译成实际交易指令。
    """

    # A 股最小交易单位
    _LOT_SIZE = 100

    def __init__(self, connection_mgr, order_mgr: OrderManager,
                 position_mgr=None):
        self._conn_mgr = connection_mgr
        self._order_mgr = order_mgr
        self._position_mgr = position_mgr

    # ------------------------------------------------------------------ 买入

    def buy_limit(self, strategy_id: str, strategy_name: str,
                  stock_code: str, price: float,
                  quantity: int, remark: str = "") -> Order:
        """限价买入（挂单）"""
        order = Order(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            stock_code=stock_code,
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT,
            price=price,
            quantity=quantity,
            remark=remark or f"限价买入 {stock_code}",
        )
        return self._submit_order(order)

    def buy_market(self, strategy_id: str, strategy_name: str,
                   stock_code: str, quantity: int, remark: str = "") -> Order:
        """市价买入（吃单）"""
        order = Order(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            stock_code=stock_code,
            direction=OrderDirection.BUY,
            order_type=OrderType.MARKET,
            price=0.0,
            quantity=quantity,
            remark=remark or f"市价买入 {stock_code}",
        )
        return self._submit_order(order)

    def buy_by_amount(self, strategy_id: str, strategy_name: str,
                      stock_code: str, price: float,
                      amount: float, remark: str = "") -> Order:
        """按金额买入（自动计算数量，取整到100股）"""
        if price <= 0:
            logger.error("buy_by_amount: price 必须 > 0")
            return self._failed_order(strategy_id, strategy_name, stock_code,
                                      OrderDirection.BUY, "price=0")
        quantity = self._calc_quantity(amount, price)
        if quantity <= 0:
            logger.warning("buy_by_amount: 金额 %.0f 不足买1手 (price=%.3f)", amount, price)
            return self._failed_order(strategy_id, strategy_name, stock_code,
                                      OrderDirection.BUY, "金额不足")
        order = Order(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            stock_code=stock_code,
            direction=OrderDirection.BUY,
            order_type=OrderType.BY_AMOUNT,
            price=price,
            quantity=quantity,
            amount=amount,
            remark=remark or f"按金额买入 {stock_code} ¥{amount:.0f}",
        )
        return self._submit_order(order)

    # ------------------------------------------------------------------ 卖出

    def sell_limit(self, strategy_id: str, strategy_name: str,
                   stock_code: str, price: float,
                   quantity: int, remark: str = "") -> Order:
        """限价卖出"""
        order = Order(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            stock_code=stock_code,
            direction=OrderDirection.SELL,
            order_type=OrderType.LIMIT,
            price=price,
            quantity=quantity,
            remark=remark or f"限价卖出 {stock_code}",
        )
        return self._submit_order(order)

    def sell_market(self, strategy_id: str, strategy_name: str,
                    stock_code: str, quantity: int, remark: str = "") -> Order:
        """市价卖出"""
        order = Order(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            stock_code=stock_code,
            direction=OrderDirection.SELL,
            order_type=OrderType.MARKET,
            price=0.0,
            quantity=quantity,
            remark=remark or f"市价卖出 {stock_code}",
        )
        return self._submit_order(order)

    def close_position(self, strategy_id: str, strategy_name: str,
                       stock_code: str, remark: str = "") -> Order:
        """平仓（卖出全部可用持仓）"""
        available = 0
        if self._position_mgr:
            pos = self._position_mgr.get_position(strategy_id)
            if pos:
                available = pos.available_quantity
        if available <= 0:
            logger.warning("close_position: 无可用持仓 strategy=%s code=%s",
                           strategy_id[:8], stock_code)
            return self._failed_order(strategy_id, strategy_name, stock_code,
                                      OrderDirection.SELL, "无可用持仓")
        return self.sell_market(strategy_id, strategy_name, stock_code, available,
                                remark=remark or f"平仓 {stock_code}")

    # ------------------------------------------------------------------ 撤单

    def cancel_order(self, order_uuid: str, remark: str = "") -> bool:
        """提交撤单请求，返回是否成功发出。"""
        order = self._order_mgr.get_order(order_uuid)
        if not order:
            logger.warning("cancel_order: 未找到订单 uuid=%s", order_uuid[:8])
            return False
        if not order.is_active():
            logger.warning("cancel_order: 订单 %s 已终结 status=%s",
                           order_uuid[:8], order.status.value)
            return False

        trader = self._conn_mgr.get_trader() if self._conn_mgr else None
        if not trader or not _XT_AVAILABLE:
            logger.warning("cancel_order [MOCK]: uuid=%s xt_id=%d",
                           order_uuid[:8], order.xt_order_id)
            order.status = OrderStatus.CANCELED
            return True

        try:
            account = self._conn_mgr.account
            trader.cancel_order_stock(account, order.xt_order_id)
            logger.info("[ORDER] 撤单提交 uuid=%s xt_id=%d remark=%s",
                        order_uuid[:8], order.xt_order_id, remark)
            return True
        except Exception as e:
            logger.error("cancel_order 失败: %s", e, exc_info=True)
            return False

    # ------------------------------------------------------------------ Internal

    def _submit_order(self, order: Order) -> Order:
        """提交订单到 xtquant，并同步注册到 ``OrderManager``。"""
        trader = self._conn_mgr.get_trader() if self._conn_mgr else None

        if not trader or not _XT_AVAILABLE:
            # Mock 模式：直接标记为待报
            order.xt_order_id = int(time.time() * 1000) % 2**31
            order.status = OrderStatus.WAIT_REPORTING
            self._order_mgr.register_order(order)
            logger.info("[ORDER] [MOCK] 下单 uuid=%s code=%s dir=%s price=%.3f qty=%d",
                        order.order_uuid[:8], order.stock_code,
                        order.direction.value, order.price, order.quantity)
            return order

        try:
            account = self._conn_mgr.account
            xt_code = self._code_to_xt(order.stock_code)
            # 内部买卖方向要转换成 xtquant 常量。
            xt_direction = (xtconstant.STOCK_BUY
                            if order.direction == OrderDirection.BUY
                            else xtconstant.STOCK_SELL)
            # 不同市场的市价单常量不同，这里按证券代码前缀自动选择。
            price_type = (xtconstant.FIX_PRICE
                          if order.order_type in (OrderType.LIMIT, OrderType.BY_AMOUNT,
                                                  OrderType.BY_QUANTITY)
                          else (xtconstant.MARKET_SH_INSTANT
                                if order.stock_code.startswith("6")
                                else xtconstant.MARKET_SZ_CONVERT))

            seq = trader.order_stock_async(
                account,
                xt_code,
                xt_direction,
                order.quantity,
                price_type,
                order.price,
                order.strategy_name,
                order.remark[:64],
            )
            order.status = OrderStatus.WAIT_REPORTING
            self._order_mgr.register_order(order)
            self._order_mgr.register_seq(seq, order.order_uuid)
            logger.info("[ORDER] 下单提交 uuid=%s seq=%d code=%s dir=%s price=%.3f qty=%d",
                        order.order_uuid[:8], seq, order.stock_code,
                        order.direction.value, order.price, order.quantity)
        except Exception as e:
            order.status = OrderStatus.JUNK
            self._order_mgr.register_order(order)
            logger.error("TradeExecutor: 下单失败 uuid=%s: %s",
                         order.order_uuid[:8], e, exc_info=True)
        return order

    @staticmethod
    def _calc_quantity(amount: float, price: float) -> int:
        """按金额计算可买数量，并向下取整到一手（100 股）。"""
        lots = math.floor(amount / price / TradeExecutor._LOT_SIZE)
        return lots * TradeExecutor._LOT_SIZE

    @staticmethod
    def _code_to_xt(code: str) -> str:
        """把内部证券代码转换为 xtquant 需要的市场代码格式。"""
        code = str(code).strip().zfill(6)
        if code.startswith(("6", "5")):
            return f"{code}.SH"
        return f"{code}.SZ"

    @staticmethod
    def _failed_order(strategy_id: str, strategy_name: str, stock_code: str,
                      direction: OrderDirection, reason: str) -> Order:
        """构造一张失败订单对象，供上层统一处理。"""
        order = Order(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            stock_code=stock_code,
            direction=direction,
            status=OrderStatus.JUNK,
            remark=f"[JUNK] {reason}",
        )
        return order


__all__ = ["TradeExecutor"]
