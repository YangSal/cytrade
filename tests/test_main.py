"""
主入口装配测试
"""
import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from datetime import datetime
from unittest.mock import patch

import main as app_main
from config.settings import Settings


class TestMainBuildApp(unittest.TestCase):

    def test_build_app_registers_reconnect_callback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                LOG_DIR=os.path.join(tmpdir, "logs"),
                SQLITE_DB_PATH=os.path.join(tmpdir, "data", "cytrade.db"),
                STATE_SAVE_DIR=os.path.join(tmpdir, "saved_states"),
            )
            ctx = app_main.build_app(strategy_classes=[], settings=settings)

            self.assertIsNotNone(ctx["conn_mgr"])
            self.assertIn(ctx["data_sub"].resubscribe_all, ctx["conn_mgr"]._reconnect_callbacks)
            self.assertIs(ctx["runner"]._heartbeat_callback.__self__, ctx["watchdog"])
            self.assertIs(
                ctx["runner"]._heartbeat_callback.__func__,
                ctx["watchdog"].register_heartbeat.__func__,
            )
            self.assertIs(ctx["runner"]._alert_callback.__self__, ctx["watchdog"])
            self.assertIs(
                ctx["runner"]._alert_callback.__func__,
                ctx["watchdog"].send_dingtalk_alert.__func__,
            )
            self.assertEqual(ctx["conn_mgr"].account_type, settings.ACCOUNT_TYPE)

    def test_run_daily_session_skips_non_trading_day(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                LOG_DIR=os.path.join(tmpdir, "logs"),
                SQLITE_DB_PATH=os.path.join(tmpdir, "data", "cytrade.db"),
                STATE_SAVE_DIR=os.path.join(tmpdir, "saved_states"),
            )

            with patch("main.is_market_day", return_value=False):
                result = app_main.run_daily_session(
                    strategy_classes=[],
                    settings=settings,
                    now_provider=lambda: datetime(2026, 3, 8, 8, 0, 0),
                    sleep_fn=lambda _: None,
                )

            self.assertEqual(result, "skipped_non_trading_day")

    def test_run_daily_session_waits_then_runs_managed_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                LOG_DIR=os.path.join(tmpdir, "logs"),
                SQLITE_DB_PATH=os.path.join(tmpdir, "data", "cytrade.db"),
                STATE_SAVE_DIR=os.path.join(tmpdir, "saved_states"),
                SESSION_START_TIME="09:25",
                SESSION_EXIT_TIME="15:05",
                SESSION_POLL_INTERVAL_SEC=1,
            )

            with patch("main.is_market_day", return_value=True), \
                 patch("main._wait_until_session_start", return_value=True) as wait_mock, \
                 patch("main._run_managed_session") as run_mock:
                result = app_main.run_daily_session(
                    strategy_classes=[],
                    settings=settings,
                    now_provider=lambda: datetime(2026, 3, 10, 9, 0, 0),
                    sleep_fn=lambda _: None,
                )

            self.assertEqual(result, "completed")
            wait_mock.assert_called_once()
            run_mock.assert_called_once()

    def test_run_scheduler_service_registers_blocking_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                LOG_DIR=os.path.join(tmpdir, "logs"),
                SQLITE_DB_PATH=os.path.join(tmpdir, "data", "cytrade.db"),
                STATE_SAVE_DIR=os.path.join(tmpdir, "saved_states"),
                SESSION_START_TIME="09:25",
            )

            scheduler = unittest.mock.MagicMock()
            scheduler_cls = unittest.mock.MagicMock(return_value=scheduler)

            with patch("main.signal.signal"):
                app_main.run_scheduler_service(
                    strategy_classes=[],
                    settings=settings,
                    scheduler_cls=scheduler_cls,
                )

            scheduler_cls.assert_called_once()
            scheduler.add_job.assert_called_once()
            scheduler.start.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
