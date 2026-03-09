"""策略数据模型。

这些模型负责描述：
- 策略创建时需要哪些配置
- 策略持久化时需要保存哪些状态
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from config.enums import StrategyStatus
from position.models import PositionInfo


@dataclass
class StrategyConfig:
    """策略开仓配置参数"""
    stock_code: str = ""
    entry_price: float = 0.0           # 参考开仓价（0 表示不限定）
    stop_loss_price: float = 0.0       # 止损价格（0 表示不使用）
    take_profit_price: float = 0.0     # 止盈价格（0 表示不使用）
    max_position_amount: float = 0.0   # 该标的最大持仓金额
    params: Dict[str, Any] = field(default_factory=dict)  # 策略特有参数


@dataclass
class StrategySnapshot:
    """策略状态快照（用于跨交易日持久化与恢复）"""
    strategy_id: str = ""
    strategy_name: str = ""
    stock_code: str = ""
    status: StrategyStatus = StrategyStatus.INITIALIZING
    config: StrategyConfig = field(default_factory=StrategyConfig)
    position: PositionInfo = field(default_factory=PositionInfo)
    pending_order_uuids: List[str] = field(default_factory=list)  # 未完结订单 UUID
    custom_state: Dict[str, Any] = field(default_factory=dict)    # 策略自定义状态
    create_time: datetime = field(default_factory=datetime.now)
    update_time: datetime = field(default_factory=datetime.now)


__all__ = ["StrategyConfig", "StrategySnapshot"]
