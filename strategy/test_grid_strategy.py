"""
高频网格测试策略
继承自 BaseStrategy，实现简单的网格交易
用于验证框架各模块的完整链路：下单、成交、撤单、持仓更新、止盈止损

网格逻辑：
- 在 [grid_low, grid_high] 区间内均分 grid_count 个价格档位
- 价格下穿某档位 → 买入
- 价格上穿某档位 → 卖出
- 每格限定最大一笔挂单，买卖交替
"""
from typing import List, Optional

from strategy.base import BaseStrategy
from strategy.models import StrategyConfig
from core.models import TickData
from monitor.logger import get_logger

logger = get_logger("trade")


class TestGridStrategy(BaseStrategy):
    """高频网格测试策略。

    这个策略的主要用途是演示和验证框架链路，
    不强调收益逻辑，而强调“流程是否完整可跑通”。
    """

    __test__ = False

    strategy_name: str = "TestGrid"
    max_positions: int = 5
    max_total_amount: float = 500_000.0

    def __init__(self, config: StrategyConfig,
                 trade_executor=None, position_manager=None):
        """初始化网格策略，并从配置中读取网格参数。"""
        super().__init__(config, trade_executor, position_manager)
        # 把策略自定义参数集中放在 config.params 中，便于不同策略自由扩展。
        p = config.params
        self._grid_count: int = int(p.get("grid_count", 10))
        self._grid_low: float = float(p.get("grid_low", 0.0))
        self._grid_high: float = float(p.get("grid_high", 0.0))
        self._per_grid_amount: float = float(p.get("per_grid_amount", 10000.0))

        self._grid_levels: List[float] = []
        self._last_price: float = 0.0
        self._grid_orders: dict = {}   # {grid_idx: order_uuid}
        self._initialized = False

    # ------------------------------------------------------------------ Override

    def on_tick(self, tick: TickData) -> Optional[dict]:
        """生成网格交易信号"""
        if not self._initialized:
            self._init_grid(tick.last_price)
            self._initialized = True
            self._last_price = tick.last_price
            return None

        signal = None
        price = tick.last_price

        # 检测价格穿越哪个网格档
        for i, level in enumerate(self._grid_levels):
            # 下穿 level：买入信号
            if self._last_price > level >= price:
                if i not in self._grid_orders:
                    signal = {
                        "action": "BUY",
                        "price": price,
                        "amount": self._per_grid_amount,
                        "remark": f"网格买入-第{i}格 price={price:.3f}"
                    }
                    logger.debug("TestGrid: 触发买入信号 grid=%d price=%.3f", i, price)
                    break
            # 上穿 level：卖出信号
            elif self._last_price < level <= price:
                pos = (self._position_mgr.get_position(self.strategy_id)
                       if self._position_mgr else None)
                if pos and pos.available_quantity > 0:
                    qty = min(int(self._per_grid_amount / price / 100) * 100,
                              pos.available_quantity)
                    if qty > 0:
                        signal = {
                            "action": "SELL",
                            "price": price,
                            "quantity": qty,
                            "remark": f"网格卖出-第{i}格 price={price:.3f}"
                        }
                        logger.debug("TestGrid: 触发卖出信号 grid=%d price=%.3f qty=%d",
                                     i, price, qty)
                        break

        self._last_price = price
        return signal

    def select_stocks(self) -> List[StrategyConfig]:
        """选股：默认返回空，外部传入配置"""
        return []

    # ------------------------------------------------------------------ Custom state

    def _get_custom_state(self) -> dict:
        """返回需要持久化保存的网格状态。"""
        return {
            "grid_levels": self._grid_levels,
            "last_price": self._last_price,
            "initialized": self._initialized,
        }

    def _restore_custom_state(self, state: dict) -> None:
        """从持久化快照中恢复网格状态。"""
        self._grid_levels = state.get("grid_levels", [])
        self._last_price = state.get("last_price", 0.0)
        self._initialized = state.get("initialized", False)

    # ------------------------------------------------------------------ Private

    def _init_grid(self, current_price: float) -> None:
        """以当前价格为中心初始化网格。

        如果没有显式给出上下边界，就默认围绕当前价上下各留 5% 区间。
        """
        low = self._grid_low or current_price * 0.95
        high = self._grid_high or current_price * 1.05
        step = (high - low) / self._grid_count
        self._grid_levels = [
            round(low + i * step, 3)
            for i in range(self._grid_count + 1)
        ]
        logger.info("TestGrid[%s] 网格初始化 low=%.3f high=%.3f count=%d",
                    self.strategy_id[:8], low, high, self._grid_count)


__all__ = ["TestGridStrategy"]
