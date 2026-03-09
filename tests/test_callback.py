"""
回调状态映射测试
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.callback import MyXtQuantTraderCallback
from config.enums import OrderStatus


class TestCallbackStatusMapping(unittest.TestCase):

    def test_on_stock_trade_contains_full_xttrade_fields(self):
        order_mgr = MagicMock()
        cb = MyXtQuantTraderCallback(order_manager=order_mgr)
        trade = SimpleNamespace(
            account_type=2,
            account_id="ACC001",
            stock_code="000001.SZ",
            order_type=23,
            traded_id="T100",
            traded_time=20260306101112,
            traded_price=10.5,
            traded_volume=200,
            traded_amount=2100.0,
            order_id=123456,
            order_sysid="SYSX1",
            strategy_name="GridA",
            order_remark="remark-x",
            direction=0,
            offset_flag=23,
        )

        cb.on_stock_trade(trade)

        order_mgr.on_trade.assert_called_once()
        xt_order_id, payload = order_mgr.on_trade.call_args.args
        self.assertEqual(xt_order_id, 123456)
        self.assertEqual(payload["account_type"], 2)
        self.assertEqual(payload["account_id"], "ACC001")
        self.assertEqual(payload["stock_code"], "000001")
        self.assertEqual(payload["order_type"], 23)
        self.assertEqual(payload["traded_id"], "T100")
        self.assertEqual(payload["traded_time"], 20260306101112)
        self.assertEqual(payload["traded_price"], 10.5)
        self.assertEqual(payload["traded_volume"], 200)
        self.assertEqual(payload["traded_amount"], 2100.0)
        self.assertEqual(payload["order_id"], 123456)
        self.assertEqual(payload["order_sysid"], "SYSX1")
        self.assertEqual(payload["strategy_name"], "GridA")
        self.assertEqual(payload["order_remark"], "remark-x")
        self.assertEqual(payload["direction"], 0)
        self.assertEqual(payload["offset_flag"], 23)
        self.assertEqual(payload["commission"], 0.0)

    def test_map_order_status_complete(self):
        mapping_cases = {
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
        for xt_status, expected in mapping_cases.items():
            with self.subTest(xt_status=xt_status):
                actual = MyXtQuantTraderCallback._map_order_status(xt_status)
                self.assertEqual(actual, expected)

    def test_map_order_status_distinguish_reported_and_reported_cancel(self):
        status_50 = MyXtQuantTraderCallback._map_order_status(50)
        status_51 = MyXtQuantTraderCallback._map_order_status(51)

        self.assertEqual(status_50, OrderStatus.REPORTED)
        self.assertEqual(status_51, OrderStatus.REPORTED_CANCEL)
        self.assertNotEqual(status_50, status_51)

    def test_map_order_status_unknown_fallback(self):
        status = MyXtQuantTraderCallback._map_order_status(999)
        self.assertEqual(status, OrderStatus.UNKNOWN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
