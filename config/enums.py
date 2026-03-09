"""
枚举类型定义
所有模块共用的枚举常量集中在这里
"""
from enum import Enum


class OrderDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"              # 限价单（挂单）
    MARKET = "MARKET"            # 市价单（吃单）
    BY_AMOUNT = "BY_AMOUNT"      # 按金额下单
    BY_QUANTITY = "BY_QUANTITY"  # 按数量下单


class OrderStatus(str, Enum):
    UNREPORTED = "UNREPORTED"                    # 未报（48）
    WAIT_REPORTING = "WAIT_REPORTING"            # 待报（49）
    REPORTED = "REPORTED"                        # 已报（50）
    REPORTED_CANCEL = "REPORTED_CANCEL"          # 已报待撤（51）
    PARTSUCC_CANCEL = "PARTSUCC_CANCEL"          # 部成待撤（52）
    PART_CANCEL = "PART_CANCEL"                  # 部撤（53）
    CANCELED = "CANCELED"                        # 已撤（54）
    PART_SUCC = "PART_SUCC"                      # 部成（55）
    SUCCEEDED = "SUCCEEDED"                      # 已成（56）
    JUNK = "JUNK"                                # 废单（57）
    UNKNOWN = "UNKNOWN"                          # 未知（255）


class StrategyStatus(str, Enum):
    INITIALIZING = "INITIALIZING"    # 初始化中
    RUNNING = "RUNNING"              # 运行中
    PAUSED = "PAUSED"                # 暂停
    STOPPED = "STOPPED"              # 已停止（持仓已清）
    ERROR = "ERROR"                  # 异常


class AlertLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class CostMethod(str, Enum):
    MOVING_AVERAGE = "moving_average"    # 移动平均成本
    FIFO = "fifo"                        # 先进先出


class SubscriptionPeriod(str, Enum):
    TICK = "tick"
    MIN1 = "1m"
    MIN5 = "5m"


__all__ = [
    'OrderDirection', 'OrderType', 'OrderStatus',
    'StrategyStatus', 'AlertLevel', 'CostMethod', 'SubscriptionPeriod',
]
