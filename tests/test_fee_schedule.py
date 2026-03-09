"""费率表测试。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.fee_schedule import FeeSchedule
from config.enums import OrderDirection


class TestFeeSchedule(unittest.TestCase):

    def test_default_rates_used_when_not_found(self):
        schedule = FeeSchedule(file_path="", default_buy_fee_rate=0.0001,
                               default_sell_fee_rate=0.0001,
                               default_stamp_tax_rate=0.0003)
        fee = schedule.calculate("000001", OrderDirection.BUY, 10000)
        self.assertEqual(fee.buy_commission, 1.0)
        self.assertEqual(fee.total_fee, 1.0)
        self.assertFalse(fee.is_t0)

    def test_rounds_up_to_cent(self):
        schedule = FeeSchedule(file_path="", default_buy_fee_rate=0.0001,
                               default_sell_fee_rate=0.0001,
                               default_stamp_tax_rate=0.0003)
        fee = schedule.calculate("000001", OrderDirection.BUY, 10001)
        self.assertEqual(fee.buy_commission, 1.01)

    def test_pattern_rule_marks_t0_and_zero_stamp_tax(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "fee_rates.csv")
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "code_pattern,buy_fee_rate,sell_fee_rate,stamp_tax_rate,is_t0,description\n"
                    "*,0.0001,0.0001,0.0003,false,default\n"
                    "159***,0.0001,0.0001,0.0,true,etf\n"
                )
            schedule = FeeSchedule(path)
            fee = schedule.calculate("159001", OrderDirection.SELL, 12345)
            self.assertTrue(fee.is_t0)
            self.assertEqual(fee.stamp_tax, 0.0)
            self.assertEqual(fee.sell_commission, 1.24)


if __name__ == "__main__":
    unittest.main(verbosity=2)