"""
XtQuant 交易回调管理
- 作为 xtquant 与框架的中介
- 所有回调均用 try-except 包裹，防止异常导致 QMT 崩溃
- 解析成交/委托信息后转发给 OrderManager / ConnectionManager
隔离外部依赖：将 xtquant 的回调机制与业务逻辑解耦。
异常安全：每个回调方法都用 try-except 包裹，防止回调中抛出的异常导致 QMT 客户端崩溃。
状态映射与数据清洗：将 xtquant 返回的原始数据（如订单状态码、股票代码格式）转换为内部枚举和标准格式。
"""
import logging
from typing import Optional

try:
    from xtquant.xttrader import XtQuantTraderCallback
    _XT_AVAILABLE = True
except ImportError:
    _XT_AVAILABLE = False

    class XtQuantTraderCallback:  # type: ignore
        """Mock 基类"""
        pass

from monitor.logger import get_logger
from config.enums import OrderStatus

logger = get_logger("system")


class MyXtQuantTraderCallback(XtQuantTraderCallback):
    """XtQuant 统一回调处理器。

    这个类是“外部交易接口”和“内部业务模块”之间的翻译层：
    - 接收 xtquant 的原始回调对象
    - 抽取关键字段
    - 转换成项目内部统一的数据格式
    - 分发给订单管理器或连接管理器
    """

    def __init__(self, order_manager=None, connection_manager=None):
        super().__init__()
        self._order_mgr = order_manager
        self._conn_mgr = connection_manager

    def set_order_manager(self, order_manager) -> None:
        """运行时替换订单管理器引用。"""
        self._order_mgr = order_manager

    def set_connection_manager(self, connection_manager) -> None:
        """运行时替换连接管理器引用。"""
        self._conn_mgr = connection_manager

    # ------------------------------------------------------------------ 回调

    def on_disconnected(self) -> None:
        """连接断开时通知连接管理器启动重连。"""
        try:
            logger.warning("[Callback] on_disconnected — 触发重连")
            if self._conn_mgr:
                self._conn_mgr.on_disconnected()
        except Exception as e:
            logger.error("[Callback] on_disconnected 异常: %s", e, exc_info=True)

    def on_stock_order(self, order) -> None:
        """委托回报（状态变化）"""
        try:
            if not self._order_mgr:
                return
            # xtquant 返回的是数字状态码，先映射成内部枚举，再交给订单管理器。
            status = self._map_order_status(order.order_status)
            self._order_mgr.update_order_status(
                xt_order_id=order.order_id,
                status=status,
                filled_qty=int(order.traded_volume or 0),
                filled_amount=float(order.traded_amount or 0),
                avg_price=float(order.traded_price or 0),
            )
            logger.debug("[Callback] on_stock_order id=%s status=%s", order.order_id, status)
        except Exception as e:
            logger.error("[Callback] on_stock_order 异常: %s", e, exc_info=True)

    def on_stock_trade(self, trade) -> None:
        """成交回报"""
        try:
            if not self._order_mgr:
                return
            # 先把 XtTrade 对象拆成普通字典，便于后续统一处理和扩展字段。
            trade_info = {
                "account_type": int(getattr(trade, "account_type", 0) or 0),
                "account_id": str(getattr(trade, "account_id", "") or ""),
                "stock_code": self._xt_to_code(str(trade.stock_code or "")),
                "order_type": int(getattr(trade, "order_type", 0) or 0),
                "traded_id": str(getattr(trade, "traded_id", "") or ""),
                "traded_time": int(getattr(trade, "traded_time", 0) or 0),
                "traded_price": float(getattr(trade, "traded_price", 0) or 0),
                "traded_volume": int(getattr(trade, "traded_volume", 0) or 0),
                "traded_amount": float(getattr(trade, "traded_amount", 0) or 0),
                "order_id": int(getattr(trade, "order_id", 0) or 0),
                "order_sysid": str(getattr(trade, "order_sysid", "") or ""),
                "strategy_name": str(getattr(trade, "strategy_name", "") or ""),
                "order_remark": str(getattr(trade, "order_remark", "") or ""),
                "direction": int(getattr(trade, "direction", 0) or 0),
                "offset_flag": int(getattr(trade, "offset_flag", 0) or 0),
                "commission": 0.0,
            }
            # 再补齐项目内部常用的统一字段名，降低后续业务层理解成本。
            trade_info.update({
                "trade_id": trade_info["traded_id"],
                "xt_order_id": trade_info["order_id"],
                "price": trade_info["traded_price"],
                "quantity": trade_info["traded_volume"],
                "amount": trade_info["traded_amount"],
            })
            self._order_mgr.on_trade(trade_info["order_id"], trade_info)
            logger.info("[ORDER] [TRADE] 成交回报 order_id=%s price=%.3f qty=%d",
                        trade_info["order_id"], trade_info["traded_price"], trade_info["traded_volume"])
        except Exception as e:
            logger.error("[Callback] on_stock_trade 异常: %s", e, exc_info=True)

    def on_order_error(self, order_error) -> None:
        """下单错误"""
        try:
            if not self._order_mgr:
                return
            xt_id = int(getattr(order_error, "order_id", 0) or 0)
            err_msg = str(getattr(order_error, "error_msg", "unknown") or "unknown")
            logger.error("[Callback] 下单失败 order_id=%s msg=%s", xt_id, err_msg)
            self._order_mgr.update_order_status(xt_order_id=xt_id, status=OrderStatus.JUNK)
        except Exception as e:
            logger.error("[Callback] on_order_error 异常: %s", e, exc_info=True)

    def on_cancel_order_error(self, cancel_error) -> None:
        """撤单错误"""
        try:
            xt_id = int(getattr(cancel_error, "order_id", 0) or 0)
            err_msg = str(getattr(cancel_error, "error_msg", "unknown") or "unknown")
            logger.warning("[Callback] 撤单失败 order_id=%s msg=%s", xt_id, err_msg)
        except Exception as e:
            logger.error("[Callback] on_cancel_order_error 异常: %s", e, exc_info=True)

    def on_order_stock_async_response(self, response) -> None:
        """异步下单响应 — 记录柜台返回的 order_id"""
        try:
            if not self._order_mgr:
                return
            seq = int(getattr(response, "seq", 0) or 0)
            xt_id = int(getattr(response, "order_id", 0) or 0)
            logger.debug("[Callback] async_response seq=%d xt_id=%d", seq, xt_id)
            self._order_mgr.on_async_response(seq, xt_id)
        except Exception as e:
            logger.error("[Callback] on_order_stock_async_response 异常: %s", e, exc_info=True)

    def on_account_status(self, status) -> None:
        """账户状态变化"""
        try:
            account_id = str(getattr(status, "account_id", "?") or "?")
            acc_status = str(getattr(status, "status", "?") or "?")
            logger.info("[Callback] 账户状态变化 account=%s status=%s", account_id, acc_status)
        except Exception as e:
            logger.error("[Callback] on_account_status 异常: %s", e, exc_info=True)

    # ------------------------------------------------------------------ Private

    @staticmethod
    def _map_order_status(xt_status) -> OrderStatus:
        """将 xtquant 委托状态映射为内部 ``OrderStatus``。"""
        mapping = {
            48: OrderStatus.UNREPORTED,
            49: OrderStatus.WAIT_REPORTING,
            50: OrderStatus.REPORTED,
            51: OrderStatus.REPORTED_CANCEL,
            52: OrderStatus.PARTSUCC_CANCEL,
            53: OrderStatus.PART_CANCEL,
            54: OrderStatus.CANCELED,
            55: OrderStatus.PART_SUCC,
            56: OrderStatus.SUCCEEDED,
            57: OrderStatus.JUNK,
            255: OrderStatus.UNKNOWN,
        }
        return mapping.get(int(xt_status or 0), OrderStatus.UNKNOWN)

    @staticmethod
    def _xt_to_code(xt_code: str) -> str:
        """把 xtquant 代码格式转换为项目内部 6 位证券代码。"""
        return xt_code.split(".")[0] if "." in xt_code else xt_code


__all__ = ["MyXtQuantTraderCallback"]
