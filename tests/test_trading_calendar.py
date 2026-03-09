"""交易日工具测试。"""
import os
import sys
import unittest
from datetime import date, datetime
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.trading_calendar import (
    TargetDate,
    add_mark_day,
    add_one_market_day,
    date_range,
    is_market_day,
    minus_one_market_day,
)


class TestTradingCalendar(unittest.TestCase):

    def tearDown(self):
        from core import trading_calendar
        trading_calendar._is_market_day_cached.cache_clear()

    @patch("core.trading_calendar.chinese_calendar.is_workday")
    def test_is_market_day_accepts_string(self, mock_is_workday):
        mock_is_workday.return_value = True
        self.assertTrue(is_market_day("2026-03-06"))

    @patch("core.trading_calendar.chinese_calendar.is_workday")
    def test_add_one_market_day_skips_weekend(self, mock_is_workday):
        mock_is_workday.side_effect = lambda d: d.weekday() < 5
        self.assertEqual(add_one_market_day("20260306"), "20260309")

    @patch("core.trading_calendar.chinese_calendar.is_workday")
    def test_minus_one_market_day_skips_weekend(self, mock_is_workday):
        mock_is_workday.side_effect = lambda d: d.weekday() < 5
        self.assertEqual(minus_one_market_day("20260309"), "20260306")

    @patch("core.trading_calendar.chinese_calendar.is_workday")
    def test_add_mark_day_supports_negative_offset(self, mock_is_workday):
        mock_is_workday.side_effect = lambda d: d.weekday() < 5
        self.assertEqual(add_mark_day("20260310", -2), "20260306")

    @patch("core.trading_calendar.chinese_calendar.is_workday")
    def test_date_range_returns_only_market_days(self, mock_is_workday):
        mock_is_workday.side_effect = lambda d: d.weekday() < 5
        self.assertEqual(
            date_range("20260305", "20260310"),
            ["20260305", "20260306", "20260309", "20260310"],
        )

    @patch("core.trading_calendar.chinese_calendar.is_workday")
    def test_target_date_uses_integrated_helpers(self, mock_is_workday):
        mock_is_workday.side_effect = lambda d: d.weekday() < 5
        target = TargetDate(datetime(2026, 3, 6, 9, 30, 0))
        self.assertEqual(target.ref_date, "20260306")
        self.assertTrue(target.is_market_day)
        self.assertEqual(target.add_market_day(1), "20260309")
