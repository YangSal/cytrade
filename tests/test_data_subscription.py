"""
数据订阅模块测试
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
