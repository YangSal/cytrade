"""
Web 路由测试
"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import MagicMock

from config.enums import OrderDirection, OrderStatus, OrderType, StrategyStatus
from trading.models import Order
from web.backend import routes


@unittest.skipUnless(hasattr(routes, "cancel_order"), "fastapi not available")
class TestWebRoutes(unittest.TestCase):

    def setUp(self):
        self.order = Order(
            strategy_id="s1",
            strategy_name="TestGrid",
            stock_code="000001",
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT,
            price=10.0,
            quantity=100,
            status=OrderStatus.WAIT_REPORTING,
        )
        self.order_mgr = MagicMock()
        self.trade_exec = MagicMock()
        self.order_mgr.get_order.return_value = self.order
        self.trade_exec.cancel_order.return_value = True

        routes._order_manager = self.order_mgr
        routes._trade_executor = self.trade_exec

    def test_cancel_order_route_uses_trade_executor(self):
        result = asyncio.run(routes.cancel_order(self.order.order_uuid))

        self.trade_exec.cancel_order.assert_called_once_with(self.order.order_uuid, remark="Web撤单")
        self.assertTrue(result.success)

    def test_get_order_contains_status_text(self):
        result = asyncio.run(routes.get_order(self.order.order_uuid))
        self.assertEqual(result.status, OrderStatus.WAIT_REPORTING.value)
        self.assertEqual(result.status_text, "待报")
        self.assertEqual(result.direction, OrderDirection.BUY.value)
        self.assertEqual(result.direction_text, "买入")
        self.assertEqual(result.order_type, OrderType.LIMIT.value)
        self.assertEqual(result.order_type_text, "限价")

    def test_get_orders_contains_status_text(self):
        self.order_mgr._orders = {self.order.order_uuid: self.order}
        result = asyncio.run(routes.get_orders(strategy_id=None))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].status, OrderStatus.WAIT_REPORTING.value)
        self.assertEqual(result[0].status_text, "待报")
        self.assertEqual(result[0].direction, OrderDirection.BUY.value)
        self.assertEqual(result[0].direction_text, "买入")
        self.assertEqual(result[0].order_type, OrderType.LIMIT.value)
        self.assertEqual(result[0].order_type_text, "限价")

    def test_get_strategies_contains_status_text(self):
        strategy = MagicMock()
        strategy.strategy_id = "sid-1"
        strategy.strategy_name = "TestGrid"
        strategy.stock_code = "000001"
        strategy.status = StrategyStatus.RUNNING

        routes._strategy_runner = MagicMock()
        routes._strategy_runner.get_all_strategies.return_value = [strategy]
        routes._position_manager = None

        result = asyncio.run(routes.get_strategies())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].status, StrategyStatus.RUNNING.value)
        self.assertEqual(result[0].status_text, "运行中")

    def test_get_trades_contains_xttrade_fields(self):
        routes._data_manager = MagicMock()
        routes._data_manager.query_trades.return_value = [{
            "trade_id": "T-1",
            "xt_order_id": 10001,
            "order_uuid": "uuid-1",
            "strategy_id": "s1",
            "strategy_name": "TestGrid",
            "stock_code": "000001",
            "account_type": 2,
            "account_id": "ACC001",
            "order_type": 23,
            "traded_time": 20260306120000,
            "order_sysid": "SYS-1",
            "order_remark": "r1",
            "xt_direction": 0,
            "offset_flag": 23,
            "direction": "BUY",
            "price": 10.1,
            "quantity": 100,
            "amount": 1010.0,
            "commission": 0.0,
            "buy_commission": 0.0,
            "sell_commission": 0.0,
            "stamp_tax": 0.0,
            "total_fee": 0.0,
            "is_t0": 0,
            "trade_time": "20260306",
        }]

        result = asyncio.run(routes.get_trades())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].trade_id, "T-1")
        self.assertEqual(result[0].account_type, 2)
        self.assertEqual(result[0].account_id, "ACC001")
        self.assertEqual(result[0].order_type, 23)
        self.assertEqual(result[0].traded_time, 20260306120000)
        self.assertEqual(result[0].order_sysid, "SYS-1")
        self.assertEqual(result[0].order_remark, "r1")
        self.assertEqual(result[0].offset_flag, 23)
        self.assertEqual(result[0].direction_text, "买入")
        self.assertFalse(result[0].is_t0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
