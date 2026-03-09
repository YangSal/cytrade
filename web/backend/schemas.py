"""Pydantic 数据模型（API 请求/响应）。

这些模型的作用是把后端返回的数据结构固定下来：
- 后端开发者知道接口应该返回哪些字段
- 前端开发者知道可以稳定依赖哪些字段
- FastAPI 可以自动生成接口文档
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from pydantic import BaseModel
except ImportError:
    class BaseModel:  # type: ignore
        pass


class StrategyInfo(BaseModel):
    """策略列表与策略详情接口使用的统一响应模型。"""
    strategy_id: str
    strategy_name: str
    stock_code: str
    status: str
    status_text: str
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_quantity: int = 0
    avg_cost: float = 0.0
    current_price: float = 0.0


class PositionDetail(BaseModel):
    """单个持仓的详细信息。"""
    strategy_id: str
    strategy_name: str
    stock_code: str
    total_quantity: int
    available_quantity: int
    is_t0: bool = False
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_ratio: float
    realized_pnl: float
    total_commission: float
    total_buy_commission: float = 0.0
    total_sell_commission: float = 0.0
    total_stamp_tax: float = 0.0
    total_fees: float = 0.0
    update_time: str


class PositionSummary(BaseModel):
    """全部持仓汇总统计信息。"""
    positions_count: int
    total_market_value: float
    total_cost: float
    total_unrealized_pnl: float
    total_realized_pnl: float
    total_commission: float
    total_buy_commission: float = 0.0
    total_sell_commission: float = 0.0
    total_stamp_tax: float = 0.0
    total_fees: float = 0.0
    total_pnl: float


class OrderInfo(BaseModel):
    """订单展示模型。"""
    order_uuid: str
    strategy_id: str
    strategy_name: str
    stock_code: str
    direction: str
    direction_text: str
    order_type: str
    order_type_text: str
    price: float
    quantity: int
    status: str
    status_text: str
    filled_quantity: int
    filled_avg_price: float
    filled_amount: float
    commission: float
    remark: str
    create_time: str
    update_time: str


class TradeInfo(BaseModel):
    """成交展示模型。

    这里既保留了本项目内部关注的字段，也保留了部分 XtTrade 原始字段，
    方便问题排查和前端扩展展示。
    """
    trade_id: str
    xt_order_id: int
    order_uuid: str
    strategy_id: str
    strategy_name: str
    stock_code: str

    account_type: int
    account_id: str
    order_type: int
    traded_time: int
    order_sysid: str
    order_remark: str
    xt_direction: int
    offset_flag: int

    direction: str
    direction_text: str
    price: float
    quantity: int
    amount: float
    commission: float
    buy_commission: float = 0.0
    sell_commission: float = 0.0
    stamp_tax: float = 0.0
    total_fee: float = 0.0
    is_t0: bool = False
    trade_time: str


class SystemStatus(BaseModel):
    """系统状态面板使用的响应模型。"""
    connected: bool
    trading_time: bool
    strategy_count: int
    active_orders: int
    cpu_pct: float = 0.0
    mem_pct: float = 0.0
    timestamp: str


class ActionResponse(BaseModel):
    """执行类接口的通用返回格式。"""
    success: bool
    message: str


__all__ = [
    "StrategyInfo", "PositionDetail", "PositionSummary",
    "OrderInfo", "TradeInfo", "SystemStatus", "ActionResponse",
]
