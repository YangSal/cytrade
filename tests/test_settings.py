"""
配置模块测试
"""
import sys, os, importlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import patch

from config.enums import SubscriptionPeriod


class TestSettingsEnv(unittest.TestCase):

    def test_settings_reads_environment_overrides(self):
        with patch.dict(os.environ, {
            "QMT_PATH": r"D:\QMT\XtMiniQmt.exe",
            "ACCOUNT_ID": "demo_account",
            "ACCOUNT_PASSWORD": "demo_password",
            "SUBSCRIPTION_PERIOD": "1m",
            "LOG_LEVEL": "DEBUG",
            "LOG_SUMMARY_MODE": "true",
            "POSITION_REPORT_TIMES": "09:40,11:40,15:10",
            "WEB_PORT": "9000",
            "SESSION_START_TIME": "09:20",
            "SESSION_EXIT_TIME": "15:10",
            "SESSION_POLL_INTERVAL_SEC": "20",
            "LOAD_PREVIOUS_STATE_ON_START": "false",
            "ENABLE_REMOTE_DB": "true",
            "DEFAULT_BUY_FEE_RATE": "0.0002",
            "DEFAULT_SELL_FEE_RATE": "0.0003",
            "DEFAULT_STAMP_TAX_RATE": "0.0004",
            "REMOTE_DB_CONFIG": '{"host":"127.0.0.1","port":5432,"dbname":"cytrade","user":"u","password":"p"}',
        }, clear=False):
            import config.settings as settings_module
            settings_module = importlib.reload(settings_module)
            settings = settings_module.Settings()

            self.assertEqual(settings.QMT_PATH, r"D:\QMT\XtMiniQmt.exe")
            self.assertEqual(settings.ACCOUNT_ID, "demo_account")
            self.assertEqual(settings.ACCOUNT_PASSWORD, "demo_password")
            self.assertEqual(settings.SUBSCRIPTION_PERIOD, SubscriptionPeriod.MIN1)
            self.assertEqual(settings.LOG_LEVEL, "DEBUG")
            self.assertTrue(settings.LOG_SUMMARY_MODE)
            self.assertEqual(settings.POSITION_REPORT_TIMES, ["09:40", "11:40", "15:10"])
            self.assertEqual(settings.WEB_PORT, 9000)
            self.assertEqual(settings.SESSION_START_TIME, "09:20")
            self.assertEqual(settings.SESSION_EXIT_TIME, "15:10")
            self.assertEqual(settings.SESSION_POLL_INTERVAL_SEC, 20)
            self.assertFalse(settings.LOAD_PREVIOUS_STATE_ON_START)
            self.assertTrue(settings.ENABLE_REMOTE_DB)
            self.assertEqual(settings.REMOTE_DB_CONFIG["host"], "127.0.0.1")
            self.assertEqual(settings.DEFAULT_BUY_FEE_RATE, 0.0002)
            self.assertEqual(settings.DEFAULT_SELL_FEE_RATE, 0.0003)
            self.assertEqual(settings.DEFAULT_STAMP_TAX_RATE, 0.0004)

    def test_invalid_environment_values_fall_back_to_defaults(self):
        with patch.dict(os.environ, {
            "SUBSCRIPTION_PERIOD": "bad-period",
            "WEB_PORT": "not-int",
            "CPU_ALERT_THRESHOLD": "not-float",
            "LOG_SUMMARY_MODE": "not-bool",
            "REMOTE_DB_CONFIG": "not-json",
        }, clear=False):
            import config.settings as settings_module
            settings_module = importlib.reload(settings_module)
            settings = settings_module.Settings()

            self.assertEqual(settings.SUBSCRIPTION_PERIOD, SubscriptionPeriod.TICK)
            self.assertEqual(settings.WEB_PORT, 8080)
            self.assertEqual(settings.CPU_ALERT_THRESHOLD, 80.0)
            self.assertFalse(settings.LOG_SUMMARY_MODE)
            self.assertEqual(settings.REMOTE_DB_CONFIG["host"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
