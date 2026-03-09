"""
持仓管理测试
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from datetime import datetime

from position.manager import PositionManager
from trading.models import TradeRecord
from config.enums import OrderDirection


def _trade(strategy_id, code, direction, price, qty, commission=0.0):
    return TradeRecord(
        trade_id=f"T-{datetime.now().timestamp()}",
        order_uuid="u1",
        strategy_id=strategy_id,
        strategy_name="TestStrategy",
        stock_code=code,
        direction=direction,
        price=price,
        quantity=qty,
        amount=price * qty,
        commission=commission,
        total_fee=commission,
    )


class TestPositionMovingAvg(unittest.TestCase):
    """移动平均成本法测试"""

    def setUp(self):
        self.mgr = PositionManager(cost_method="moving_average")

    def test_buy_updates_position(self):
        t = _trade("s1", "000001", OrderDirection.BUY, 10.0, 1000)
        self.mgr.on_trade_callback(t)
        pos = self.mgr.get_position("s1")
        self.assertEqual(pos.total_quantity, 1000)
        self.assertAlmostEqual(pos.avg_cost, 10.0)
        self.assertEqual(pos.available_quantity, 0)

    def test_buy_twice_moving_avg(self):
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 10.0, 1000))
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 12.0, 500))
        pos = self.mgr.get_position("s1")
        self.assertEqual(pos.total_quantity, 1500)
        expected = (10.0 * 1000 + 12.0 * 500) / 1500
        self.assertAlmostEqual(pos.avg_cost, expected, places=4)

    def test_sell_realized_pnl(self):
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 10.0, 1000))
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.SELL, 11.0, 500))
        pos = self.mgr.get_position("s1")
        self.assertEqual(pos.total_quantity, 500)
        self.assertAlmostEqual(pos.realized_pnl, 500.0)  # (11-10)*500

    def test_full_close(self):
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 10.0, 100))
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.SELL, 10.0, 100))
        pos = self.mgr.get_position("s1")
        self.assertEqual(pos.total_quantity, 0)
        self.assertAlmostEqual(pos.avg_cost, 0.0)

    def test_price_update(self):
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 10.0, 1000))
        self.mgr.update_price("000001", 11.0)
        pos = self.mgr.get_position("s1")
        self.assertAlmostEqual(pos.market_value, 11000.0)
        self.assertAlmostEqual(pos.unrealized_pnl, 1000.0)

    def test_summary(self):
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 10.0, 1000))
        self.mgr.on_trade_callback(_trade("s2", "600000", OrderDirection.BUY, 20.0, 500))
        s = self.mgr.get_position_summary()
        self.assertEqual(s["positions_count"], 2)

    def test_buy_fee_included_in_cost(self):
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 10.0, 100, commission=1.0))
        pos = self.mgr.get_position("s1")
        self.assertAlmostEqual(pos.total_cost, 1001.0)
        self.assertAlmostEqual(pos.avg_cost, 10.01)
        self.assertAlmostEqual(pos.total_fees, 1.0)

    def test_sell_fee_reduces_realized_pnl(self):
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 10.0, 100, commission=1.0))
        self.mgr.restore_position("s1", self.mgr.get_position("s1"))
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.SELL, 11.0, 100, commission=2.0))
        pos = self.mgr.get_position("s1")
        self.assertAlmostEqual(pos.realized_pnl, 97.0)

    def test_t0_buy_increases_available_quantity(self):
        fee_schedule = type("FeeScheduleStub", (), {"is_t0_security": staticmethod(lambda code: True)})()
        mgr = PositionManager(cost_method="moving_average", fee_schedule=fee_schedule)
        t = _trade("s1", "159001", OrderDirection.BUY, 1.0, 100)
        mgr.on_trade_callback(t)
        pos = mgr.get_position("s1")
        self.assertTrue(pos.is_t0)
        self.assertEqual(pos.available_quantity, 100)


class TestPositionFifo(unittest.TestCase):
    """先进先出成本法测试"""

    def setUp(self):
        self.mgr = PositionManager(cost_method="fifo")

    def test_fifo_cost(self):
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 10.0, 500))
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 12.0, 500))
        # 卖出500股，FIFO 取第一批（成本10元）
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.SELL, 11.0, 500))
        pos = self.mgr.get_position("s1")
        self.assertAlmostEqual(pos.realized_pnl, 500.0)  # (11-10)*500

    def test_fifo_partial_lot(self):
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 10.0, 300))
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.BUY, 12.0, 300))
        # 卖出400 = 300@10 + 100@12
        self.mgr.on_trade_callback(_trade("s1", "000001", OrderDirection.SELL, 13.0, 400))
        pos = self.mgr.get_position("s1")
        expected_pnl = (13 - 10) * 300 + (13 - 12) * 100
        self.assertAlmostEqual(pos.realized_pnl, expected_pnl)


if __name__ == "__main__":
    unittest.main(verbosity=2)
