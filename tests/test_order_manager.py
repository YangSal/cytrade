"""
订单管理模块测试
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import MagicMock
from types import SimpleNamespace

from trading.order_manager import OrderManager
from trading.models import Order
from config.enums import OrderDirection, OrderType, OrderStatus


def _mk_order(strategy_id="s1", xt_order_id=0):
    return Order(
        strategy_id=strategy_id,
        strategy_name="TestStrategy",
        stock_code="000001",
        direction=OrderDirection.BUY,
        order_type=OrderType.LIMIT,
        price=10.0,
        quantity=100,
        xt_order_id=xt_order_id,
        status=OrderStatus.WAIT_REPORTING,
    )


class TestOrderManager(unittest.TestCase):

    def setUp(self):
        self.data_mgr = MagicMock()
        self.order_mgr = OrderManager(data_manager=self.data_mgr)

    def test_register_and_get_order(self):
        order = _mk_order(xt_order_id=123)
        self.order_mgr.register_order(order)
        found = self.order_mgr.get_order(order.order_uuid)
        self.assertIsNotNone(found)
        self.assertEqual(found.xt_order_id, 123)

    def test_update_order_status(self):
        order = _mk_order(xt_order_id=1001)
        self.order_mgr.register_order(order)
        self.order_mgr.update_order_status(
            xt_order_id=1001,
            status=OrderStatus.PART_SUCC,
            filled_qty=50,
            filled_amount=500.0,
            avg_price=10.0,
        )
        updated = self.order_mgr.get_order(order.order_uuid)
        self.assertEqual(updated.status, OrderStatus.PART_SUCC)
        self.assertEqual(updated.filled_quantity, 50)

    def test_on_trade_triggers_callbacks(self):
        order = _mk_order(xt_order_id=2002)
        self.order_mgr.register_order(order)

        pos_cb = MagicMock()
        strategy_cb = MagicMock()
        self.order_mgr.set_position_callback(pos_cb)
        self.order_mgr.set_strategy_callback(strategy_cb)

        trade_info = {
            "trade_id": "T1",
            "xt_order_id": 2002,
            "stock_code": "000001",
            "direction": "BUY",
            "price": 10.0,
            "quantity": 100,
            "amount": 1000.0,
            "commission": 1.0,
        }
        self.order_mgr.on_trade(2002, trade_info)

        pos_cb.assert_called_once()
        strategy_cb.assert_called_once()
        updated = self.order_mgr.get_order(order.order_uuid)
        self.assertEqual(updated.status, OrderStatus.SUCCEEDED)

    def test_async_response_binds_xt_id(self):
        order = _mk_order(xt_order_id=0)
        self.order_mgr.register_order(order)
        self.order_mgr.register_seq(77, order.order_uuid)

        self.order_mgr.on_async_response(77, 99001)

        by_xt = self.order_mgr.get_order_by_xt_id(99001)
        self.assertIsNotNone(by_xt)
        self.assertEqual(by_xt.order_uuid, order.order_uuid)

    def test_get_orders_by_strategy(self):
        o1 = _mk_order(strategy_id="s1")
        o2 = _mk_order(strategy_id="s2")
        self.order_mgr.register_order(o1)
        self.order_mgr.register_order(o2)

        s1_orders = self.order_mgr.get_orders_by_strategy("s1")
        self.assertEqual(len(s1_orders), 1)
        self.assertEqual(s1_orders[0].strategy_id, "s1")

    def test_get_active_orders_with_reported_cancel(self):
        order = _mk_order(xt_order_id=3003)
        order.status = OrderStatus.REPORTED_CANCEL
        self.order_mgr.register_order(order)

        active_orders = self.order_mgr.get_active_orders()
        active_ids = {o.order_uuid for o in active_orders}
        self.assertIn(order.order_uuid, active_ids)

    def test_on_trade_infers_sell_from_offset_flag(self):
        order = _mk_order(xt_order_id=4004)
        self.order_mgr.register_order(order)

        pos_cb = MagicMock()
        self.order_mgr.set_position_callback(pos_cb)

        trade_info = {
            "account_type": 2,
            "account_id": "ACC001",
            "stock_code": "000001",
            "order_type": 24,
            "traded_id": "TS1",
            "traded_time": 20260306110000,
            "traded_price": 10.2,
            "traded_volume": 100,
            "traded_amount": 1020.0,
            "order_id": 4004,
            "order_sysid": "SYS-4004",
            "strategy_name": "TestStrategy",
            "order_remark": "sell",
            "direction": 0,
            "offset_flag": 24,
            "commission": 1.0,
        }

        self.order_mgr.on_trade(4004, trade_info)

        pos_cb.assert_called_once()
        trade = pos_cb.call_args.args[0]
        self.assertEqual(trade.direction, OrderDirection.SELL)
        self.assertEqual(trade.order_type, 24)
        self.assertEqual(trade.offset_flag, 24)
        self.assertEqual(trade.xt_traded_time, 20260306110000)

    def test_on_trade_applies_fee_schedule(self):
        fee_schedule = MagicMock()
        fee_schedule.calculate.return_value = SimpleNamespace(
            buy_commission=1.0,
            sell_commission=0.0,
            stamp_tax=0.0,
            total_fee=1.0,
            is_t0=False,
        )
        order_mgr = OrderManager(data_manager=self.data_mgr, fee_schedule=fee_schedule)
        order = _mk_order(xt_order_id=5005)
        order_mgr.register_order(order)

        pos_cb = MagicMock()
        order_mgr.set_position_callback(pos_cb)
        order_mgr.on_trade(5005, {
            "trade_id": "T5005",
            "stock_code": "000001",
            "direction": "BUY",
            "price": 10.0,
            "quantity": 100,
            "amount": 1000.0,
        })

        trade = pos_cb.call_args.args[0]
        self.assertEqual(trade.buy_commission, 1.0)
        self.assertEqual(trade.total_fee, 1.0)
        self.assertEqual(order_mgr.get_order(order.order_uuid).commission, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
