"""交易相关展示映射。

后端内部通常使用英文状态值，前端展示时再映射成中文。
这样既方便程序内部判断，也方便界面直接显示给用户。
"""

from config.enums import OrderStatus

ORDER_STATUS_TEXT = {
    OrderStatus.UNREPORTED.value: "未报",
    OrderStatus.WAIT_REPORTING.value: "待报",
    OrderStatus.REPORTED.value: "已报",
    OrderStatus.REPORTED_CANCEL.value: "已报待撤",
    OrderStatus.PARTSUCC_CANCEL.value: "部成待撤",
    OrderStatus.PART_CANCEL.value: "部撤",
    OrderStatus.CANCELED.value: "已撤",
    OrderStatus.PART_SUCC.value: "部成",
    OrderStatus.SUCCEEDED.value: "已成",
    OrderStatus.JUNK.value: "废单",
    OrderStatus.UNKNOWN.value: "未知",
}

STRATEGY_STATUS_TEXT = {
    "INITIALIZING": "初始化中",
    "RUNNING": "运行中",
    "PAUSED": "暂停",
    "STOPPED": "已停止",
    "ERROR": "异常",
}

ORDER_DIRECTION_TEXT = {
    "BUY": "买入",
    "SELL": "卖出",
}

ORDER_TYPE_TEXT = {
    "LIMIT": "限价",
    "MARKET": "市价",
    "BY_AMOUNT": "按金额",
    "BY_QUANTITY": "按数量",
}


def order_status_text(status: str) -> str:
    """将内部订单状态值映射为中文展示文案"""
    return ORDER_STATUS_TEXT.get(str(status), str(status))


def strategy_status_text(status: str) -> str:
    """将策略状态值映射为中文展示文案"""
    return STRATEGY_STATUS_TEXT.get(str(status), str(status))


def order_direction_text(direction: str) -> str:
    """将买卖方向映射为中文展示文案"""
    return ORDER_DIRECTION_TEXT.get(str(direction), str(direction))


def order_type_text(order_type: str) -> str:
    """将委托类型映射为中文展示文案"""
    return ORDER_TYPE_TEXT.get(str(order_type), str(order_type))


__all__ = [
    "ORDER_STATUS_TEXT",
    "STRATEGY_STATUS_TEXT",
    "ORDER_DIRECTION_TEXT",
    "ORDER_TYPE_TEXT",
    "order_status_text",
    "strategy_status_text",
    "order_direction_text",
    "order_type_text",
]
