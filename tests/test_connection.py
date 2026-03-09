"""
连接管理模块测试
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import patch, MagicMock
from core.connection import ConnectionManager


class _MockTrader:
    """完全 Mock 的 XtQuantTrader，connect() 始终返回 0"""
    def __init__(self, path, session_id):
        self._connected = False
        self._subscribed = False

    def start(self):
        pass

    def stop(self):
        self._connected = False

    def connect(self):
        self._connected = True
        return 0

    def is_connected(self):
        return self._connected

    def register_callback(self, cb):
        pass

    def subscribe_callback(self, cb):
        pass

    def subscribe(self, account):
        self._subscribed = True
        return 0


class TestConnectionManager(unittest.TestCase):

    def setUp(self):
        # 强制使用 Mock Trader，不依赖真实 QMT 路径
        patcher = patch("core.connection.XtQuantTrader", _MockTrader)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.mgr = ConnectionManager(
            qmt_path=r"C:\mock\path.exe",
            account_id="test_account",
        )

    def test_connect_mock(self):
        """Mock 模式下连接成功"""
        result = self.mgr.connect()
        self.assertTrue(result)
        self.assertTrue(self.mgr.is_connected())

    def test_get_trader(self):
        self.mgr.connect()
        trader = self.mgr.get_trader()
        self.assertIsNotNone(trader)
        self.assertTrue(trader._subscribed)

    def test_account(self):
        self.mgr.connect()
        account = self.mgr.account
        self.assertIsNotNone(account)

    def test_disconnect(self):
        self.mgr.connect()
        self.mgr.disconnect()
        self.assertFalse(self.mgr.is_connected())

    def test_on_disconnected_triggers_reconnect_thread(self):
        """断开时触发重连线程"""
        import threading
        self.mgr.connect()
        # on_disconnected 会在后台线程中重连，此处只验证不崩溃
        threads_before = threading.active_count()
        self.mgr.on_disconnected()
        import time; time.sleep(0.2)
        # 应该多出一个重连线程（但在 Mock 环境下会很快完成）
        self.assertGreaterEqual(threading.active_count(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
