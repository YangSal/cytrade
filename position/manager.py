"""
持仓管理模块
- 通过成交回调实时更新持仓（不直接调用）
- 支持移动平均成本法 & FIFO
- 内存字典维护，同时通知 DataManager 归档历史盈亏
"""
import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional

from position.models import PositionInfo, FifoLot
from trading.models import TradeRecord
from config.enums import OrderDirection, CostMethod
from monitor.logger import get_logger

logger = get_logger("trade")


class PositionManager:
    """持仓管理器。

    它只根据“成交结果”更新持仓，不直接发单。
    这样可以保证持仓状态始终以真实成交为准，而不是以委托为准。
    """

    def __init__(self, cost_method: str = "moving_average", data_manager=None, fee_schedule=None):
        self._positions: Dict[str, PositionInfo] = {}   # {strategy_id: PositionInfo}
        self._cost_method = CostMethod(cost_method)
        self._data_mgr = data_manager
        self._fee_schedule = fee_schedule
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 成交回调

    def on_trade_callback(self, trade: TradeRecord) -> None:
        """由 OrderManager 触发；实时更新持仓"""
        try:
            strategy_id = trade.strategy_id
            with self._lock:
                if strategy_id not in self._positions:
                    pos = PositionInfo(
                        strategy_id=strategy_id,
                        strategy_name=trade.strategy_name,
                        stock_code=trade.stock_code,
                        is_t0=self._resolve_is_t0(trade.stock_code, trade),
                    )
                    self._positions[strategy_id] = pos
                else:
                    pos = self._positions[strategy_id]
                    pos.is_t0 = self._resolve_is_t0(pos.stock_code, trade)

                if trade.direction == OrderDirection.BUY:
                    self._apply_buy(pos, trade)
                else:
                    self._apply_sell(pos, trade)

                pos.total_buy_commission += float(getattr(trade, "buy_commission", 0.0) or 0.0)
                pos.total_sell_commission += float(getattr(trade, "sell_commission", 0.0) or 0.0)
                pos.total_stamp_tax += float(getattr(trade, "stamp_tax", 0.0) or 0.0)
                pos.total_fees += self._trade_total_fee(trade)
                pos.total_commission = pos.total_fees
                pos.update_time = datetime.now()

            logger.info(
                "PositionManager: 持仓更新 strategy=%s code=%s qty=%d avg_cost=%.3f "
                "unrealized_pnl=%.2f realized_pnl=%.2f",
                strategy_id[:8], pos.stock_code, pos.total_quantity,
                pos.avg_cost, pos.unrealized_pnl, pos.realized_pnl
            )

        except Exception as e:
            logger.error("PositionManager: on_trade_callback 异常: %s", e, exc_info=True)

    def update_price(self, stock_code: str, price: float) -> None:
        """更新指定证券的最新价格，并重算浮动盈亏。"""
        with self._lock:
            for pos in self._positions.values():
                if pos.stock_code == stock_code and pos.total_quantity > 0:
                    pos.refresh_market_value(price)

    # ------------------------------------------------------------------ 查询

    def get_position(self, strategy_id: str) -> Optional[PositionInfo]:
        """按策略 ID 获取单个持仓。"""
        with self._lock:
            return self._positions.get(strategy_id)

    def get_all_positions(self) -> Dict[str, PositionInfo]:
        """返回全部持仓的浅拷贝。"""
        with self._lock:
            return dict(self._positions)

    def get_position_summary(self) -> dict:
        """持仓汇总统计"""
        with self._lock:
            total_market = sum(p.market_value for p in self._positions.values())
            total_cost = sum(p.total_cost for p in self._positions.values())
            total_unrealized = sum(p.unrealized_pnl for p in self._positions.values())
            total_realized = sum(p.realized_pnl for p in self._positions.values())
            total_commission = sum(p.total_commission for p in self._positions.values())
            total_buy_commission = sum(p.total_buy_commission for p in self._positions.values())
            total_sell_commission = sum(p.total_sell_commission for p in self._positions.values())
            total_stamp_tax = sum(p.total_stamp_tax for p in self._positions.values())
            return {
                "positions_count": len(self._positions),
                "total_market_value": total_market,
                "total_cost": total_cost,
                "total_unrealized_pnl": total_unrealized,
                "total_realized_pnl": total_realized,
                "total_commission": total_commission,
                "total_buy_commission": total_buy_commission,
                "total_sell_commission": total_sell_commission,
                "total_stamp_tax": total_stamp_tax,
                "total_fees": total_commission,
                "total_pnl": total_unrealized + total_realized,
            }

    def remove_position(self, strategy_id: str) -> None:
        """策略清仓后归档盈亏，并移除内存持仓"""
        with self._lock:
            pos = self._positions.pop(strategy_id, None)
        if pos and self._data_mgr:
            try:
                pnl_info = {
                    "total_profit": pos.realized_pnl,
                    "total_commission": pos.total_commission,
                    "end_time": datetime.now().isoformat(),
                }
                self._data_mgr.save_strategy_pnl(
                    pos.strategy_id, pos.strategy_name, pos.stock_code, pnl_info
                )
                logger.info("PositionManager: 策略 %s 盈亏已归档", strategy_id[:8])
            except Exception as e:
                logger.error("PositionManager: 盈亏归档失败: %s", e, exc_info=True)

    def restore_position(self, strategy_id: str, position: PositionInfo) -> None:
        """恢复快照中的持仓（带锁保护）"""
        with self._lock:
            position.is_t0 = self._resolve_is_t0(position.stock_code, position)
            position.available_quantity = position.total_quantity
            position.total_commission = position.total_fees or position.total_commission
            self._positions[strategy_id] = position
        logger.info(
            "PositionManager: 持仓已恢复 strategy=%s code=%s qty=%d avg_cost=%.3f",
            strategy_id[:8], position.stock_code, position.total_quantity, position.avg_cost
        )

    # ------------------------------------------------------------------ PRIVATE

    def _apply_buy(self, pos: PositionInfo, trade: TradeRecord) -> None:
        """处理买入成交对持仓的影响。"""
        qty = trade.quantity
        price = trade.price
        amount = trade.amount or (price * qty)
        total_fee = self._trade_total_fee(trade)

        if self._cost_method == CostMethod.MOVING_AVERAGE:
            # 移动平均法下，总成本增加，再用总成本 / 总股数得到新均价。
            pos.total_cost += amount + total_fee
            pos.total_quantity += qty
            if pos.is_t0:
                pos.available_quantity += qty
            pos.avg_cost = pos.total_cost / pos.total_quantity if pos.total_quantity > 0 else 0
        else:  # FIFO
            # FIFO 需要把每次买入拆成独立批次，卖出时再一批批扣减。
            lot_cost = (amount + total_fee) / qty if qty > 0 else price
            pos.fifo_lots.append(FifoLot(quantity=qty, cost_price=lot_cost))
            pos.total_cost += amount + total_fee
            pos.total_quantity += qty
            if pos.is_t0:
                pos.available_quantity += qty
            pos.avg_cost = pos.total_cost / pos.total_quantity if pos.total_quantity > 0 else 0

    def _apply_sell(self, pos: PositionInfo, trade: TradeRecord) -> None:
        """处理卖出成交对持仓的影响。"""
        qty = trade.quantity
        price = trade.price
        amount = trade.amount or (price * qty)
        total_fee = self._trade_total_fee(trade)
        net_amount = amount - total_fee

        if self._cost_method == CostMethod.MOVING_AVERAGE:
            # 移动平均法：卖出成本 = 当前均价 * 卖出数量。
            cost_sold = pos.avg_cost * qty
            profit = net_amount - cost_sold
            pos.realized_pnl += profit
            pos.total_cost -= cost_sold
            pos.total_quantity -= qty
            pos.available_quantity = max(0, pos.available_quantity - qty)
            if pos.total_quantity <= 0:
                pos.total_cost = 0
                pos.avg_cost = 0
                pos.total_quantity = 0
                pos.available_quantity = 0
        else:  # FIFO
            # FIFO：从最早买入的批次开始逐批扣减，统计实际成本基础。
            remaining = qty
            cost_basis = 0.0
            while remaining > 0 and pos.fifo_lots:
                lot = pos.fifo_lots[0]
                take = min(lot.quantity, remaining)
                cost_basis += take * lot.cost_price
                lot.quantity -= take
                remaining -= take
                if lot.quantity == 0:
                    pos.fifo_lots.pop(0)
            profit = net_amount - cost_basis
            pos.realized_pnl += profit
            pos.total_quantity -= qty
            pos.available_quantity = max(0, pos.available_quantity - qty)
            pos.total_cost = sum(l.quantity * l.cost_price for l in pos.fifo_lots)
            pos.avg_cost = pos.total_cost / pos.total_quantity if pos.total_quantity > 0 else 0

    def _trade_total_fee(self, trade: TradeRecord) -> float:
        """获取一笔成交应计入持仓成本的总费用。"""
        total_fee = float(getattr(trade, "total_fee", 0.0) or 0.0)
        if total_fee > 0:
            return total_fee
        return float(getattr(trade, "commission", 0.0) or 0.0)

    def _resolve_is_t0(self, stock_code: str, trade_or_position) -> bool:
        """确定证券是否按 T+0 规则处理。

        优先级：
        1. 成交/持仓对象上的显式 ``is_t0`` 标记。
        2. 费率表中的证券属性配置。
        3. 最终回退为 ``False``。
        """
        explicit = getattr(trade_or_position, "is_t0", None)
        if explicit is True:
            return True
        if self._fee_schedule:
            return self._fee_schedule.is_t0_security(stock_code)
        return bool(explicit)


__all__ = ["PositionManager"]
