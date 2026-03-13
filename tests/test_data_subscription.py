"""
数据订阅模块测试
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from config.enums import SubscriptionPeriod
from core.data_subscription import DataSubscriptionManager


class TestDataSubscriptionManager(unittest.TestCase):

    def test_resubscribe_all_restores_grouped_and_whole_market_subscriptions(self):
        mgr = DataSubscriptionManager()
        mgr._subscriptions = {
            "000001": "tick",
            "000002": "1m",
            "600000": "tick",
        }
        mgr._whole_market = True
        mgr.subscribe_stocks = MagicMock()
        mgr.subscribe_whole_market = MagicMock()

        mgr.resubscribe_all()

        mgr.subscribe_whole_market.assert_called_once_with()
        calls = mgr.subscribe_stocks.call_args_list
        self.assertEqual(len(calls), 2)

        actual = {(tuple(args[0]), args[1]) for args, _ in calls}
        self.assertEqual(actual, {
            (("000001", "600000"), "tick"),
            (("000002",), "1m"),
        })

    def test_subscribe_and_unsubscribe_use_subscription_id(self):
        mgr = DataSubscriptionManager()
        fake_xtdata = MagicMock()
        fake_xtdata.subscribe_quote.return_value = 101

        with patch('core.data_subscription._XT_AVAILABLE', True), \
             patch('core.data_subscription.xtdata', fake_xtdata):
            mgr.subscribe_stocks(['000001'], 'tick')
            fake_xtdata.subscribe_quote.assert_called_once_with(
                '000001.SZ', period='tick', count=-1, callback=mgr._on_data
            )

            mgr.unsubscribe_stocks(['000001'])
            fake_xtdata.unsubscribe_quote.assert_called_once_with(101)

    def test_default_period_accepts_enum(self):
        mgr = DataSubscriptionManager(default_period=SubscriptionPeriod.MIN1)
        self.assertEqual(mgr._default_period, "1m")

    def test_invalid_period_falls_back_to_tick(self):
        mgr = DataSubscriptionManager(default_period="bad-period")
        self.assertEqual(mgr._default_period, "tick")

    def test_parse_tick_accepts_list_of_dict_payload(self):
        recv_time = datetime(2026, 3, 13, 11, 15, 0)

        tick = DataSubscriptionManager._parse_tick(
            "159981",
            [
                {"lastPrice": 1.640, "volume": 100},
                {
                    "time": 1760000000000,
                    "lastPrice": 1.641,
                    "open": 1.620,
                    "high": 1.650,
                    "low": 1.618,
                    "lastClose": 1.619,
                    "volume": 600,
                    "amount": 984.6,
                    "bidPrice": [1.640, 1.639, 1.638, 1.637, 1.636],
                    "bidVol": [100, 90, 80, 70, 60],
                    "askPrice": [1.641, 1.642, 1.643, 1.644, 1.645],
                    "askVol": [110, 120, 130, 140, 150],
                },
            ],
            recv_time,
        )

        self.assertEqual(tick.stock_code, "159981")
        self.assertEqual(tick.last_price, 1.641)
        self.assertEqual(tick.volume, 600)
        self.assertEqual(tick.bid_prices[:2], [1.64, 1.639])
        self.assertEqual(tick.ask_volumes[:2], [110, 120])

    def test_parse_tick_accepts_field_history_lists(self):
        recv_time = datetime(2026, 3, 13, 11, 15, 0)

        tick = DataSubscriptionManager._parse_tick(
            "159981",
            {
                "time": [1759999999000, 1760000000000],
                "lastPrice": [1.639, 1.641],
                "open": [1.620, 1.620],
                "high": [1.648, 1.650],
                "low": [1.618, 1.618],
                "lastClose": [1.619, 1.619],
                "volume": [300, 600],
                "amount": [492.3, 984.6],
                "bidPrice": [[1.639, 1.638, 1.637], [1.640, 1.639, 1.638]],
                "bidVol": [[90, 80, 70], [100, 90, 80]],
                "askPrice": [[1.640, 1.641, 1.642], [1.641, 1.642, 1.643]],
                "askVol": [[100, 110, 120], [110, 120, 130]],
            },
            recv_time,
        )

        self.assertEqual(tick.last_price, 1.641)
        self.assertEqual(tick.volume, 600)
        self.assertEqual(tick.bid_prices, [1.64, 1.639, 1.638])
        self.assertEqual(tick.bid_volumes, [100, 90, 80])
        self.assertEqual(tick.ask_prices, [1.641, 1.642, 1.643])
        self.assertEqual(tick.ask_volumes, [110, 120, 130])


if __name__ == "__main__":
    unittest.main(verbosity=2)
