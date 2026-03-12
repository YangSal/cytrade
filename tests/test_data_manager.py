"""
数据管理模块测试
"""
import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch

from data.manager import DataManager
from trading.models import Order, TradeRecord
from config.enums import OrderDirection, OrderType, OrderStatus


# 模块级别的 FakeSnap，可被 pickle 序列化（本地 class 无法被 pickle）
class _FakeSnap:
    """用于测试 pickle 持久化的假快照对象"""
    pass


class TestDataManager(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.mgr = DataManager(
            db_path=os.path.join(self._tmp, "test.db"),
            state_dir=self._tmp,
        )

    def test_save_and_query_trade(self):
        trade = TradeRecord(
            trade_id="T001",
            order_uuid="uuid-abc",
            xt_order_id=12345,
            strategy_id="s1",
            strategy_name="TestGrid",
            stock_code="000001",
            direction=OrderDirection.BUY,
            price=10.5,
            quantity=1000,
            amount=10500.0,
            commission=10.0,
            buy_commission=10.0,
            total_fee=10.0,
        )
        self.mgr.save_trade(trade)
        trades = self.mgr.query_trades(strategy_id="s1")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["trade_id"], "T001")
        self.assertEqual(trades[0]["buy_commission"], 10.0)
        self.assertEqual(trades[0]["total_fee"], 10.0)

    def test_save_and_query_order(self):
        order = Order(
            strategy_id="s1", strategy_name="TestGrid",
            stock_code="000001",
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT,
            price=10.0, quantity=100,
            status=OrderStatus.WAIT_REPORTING,
        )
        self.mgr.save_order(order)
        orders = self.mgr.query_orders(strategy_id="s1")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["stock_code"], "000001")

    def test_update_order(self):
        order = Order(
            strategy_id="s1", strategy_name="TestGrid",
            stock_code="000001",
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT,
            price=10.0, quantity=100,
            status=OrderStatus.WAIT_REPORTING,
        )
        self.mgr.save_order(order)
        order.status = OrderStatus.SUCCEEDED
        order.filled_quantity = 100
        self.mgr.save_order(order)
        orders = self.mgr.query_orders(strategy_id="s1")
        self.assertEqual(orders[0]["status"], "SUCCEEDED")

    def test_state_save_load(self):
        snaps = [_FakeSnap(), _FakeSnap()]
        self.mgr.save_strategy_state(snaps)
        loaded = self.mgr.load_strategy_state()
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded), 2)

    def test_load_no_state(self):
        loaded = self.mgr.load_strategy_state()
        self.assertIsNone(loaded)

    def test_load_state_falls_back_to_previous_market_day(self):
        snaps = [_FakeSnap()]
        self.mgr.save_strategy_state(snaps, trading_day="20260310")

        with patch("data.manager.minus_one_market_day", return_value="20260310"):
            loaded = self.mgr.load_strategy_state(
                trading_day="20260311",
                fallback_previous_market_day=True,
            )

        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded), 1)

    def test_query_trades_by_yyyymmdd(self):
        trade = TradeRecord(
            trade_id="T002",
            order_uuid="uuid-date",
            xt_order_id=12346,
            strategy_id="s-date",
            strategy_name="TestGrid",
            stock_code="000001",
            direction=OrderDirection.BUY,
            price=10.0,
            quantity=100,
            amount=1000.0,
            commission=1.0,
            buy_commission=1.0,
            total_fee=1.0,
            trade_time=datetime(2026, 3, 2, 9, 31, 0),
        )
        self.mgr.save_trade(trade)

        result = self.mgr.query_trades(strategy_id="s-date", start_date="20260302", end_date="20260302")
        self.assertEqual(len(result), 1)
        self.assertEqual(str(result[0]["trade_time"]), "20260302")

    def test_xt_order_id_persisted_as_integer(self):
        order = Order(
            strategy_id="s1", strategy_name="TestGrid",
            stock_code="000001",
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT,
            price=10.0, quantity=100,
            xt_order_id=123456,
            status=OrderStatus.WAIT_REPORTING,
        )
        self.mgr.save_order(order)

        with self.mgr._get_conn() as conn:
            row = conn.execute("SELECT xt_order_id, typeof(xt_order_id) AS t FROM orders").fetchone()

        self.assertEqual(row[0], 123456)
        self.assertEqual(row[1], "integer")

    def test_migrate_legacy_xt_order_id_text_columns(self):
        legacy_db = os.path.join(self._tmp, "legacy.db")
        with sqlite3.connect(legacy_db) as conn:
            conn.executescript("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT,
                order_uuid TEXT NOT NULL,
                xt_order_id TEXT,
                strategy_name TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                direction TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                amount REAL NOT NULL,
                commission REAL DEFAULT 0,
                remark TEXT,
                trade_time TIMESTAMP,
                create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_uuid TEXT NOT NULL UNIQUE,
                xt_order_id TEXT,
                strategy_name TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                direction TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                quantity INTEGER,
                amount REAL,
                status TEXT NOT NULL,
                filled_quantity INTEGER DEFAULT 0,
                filled_amount REAL DEFAULT 0,
                filled_avg_price REAL DEFAULT 0,
                commission REAL DEFAULT 0,
                remark TEXT,
                create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO trades (
                trade_id, order_uuid, xt_order_id, strategy_name, strategy_id,
                stock_code, direction, quantity, price, amount, commission, trade_time
            ) VALUES ('T-LEG', 'uuid-leg', '1001', 'TestGrid', 's1', '000001', 'BUY', 100, 10.0, 1000.0, 1.0, '20260302');
            INSERT INTO orders (
                order_uuid, xt_order_id, strategy_name, strategy_id, stock_code,
                direction, order_type, price, quantity, amount, status
            ) VALUES ('uuid-leg', '1001', 'TestGrid', 's1', '000001', 'BUY', 'LIMIT', 10.0, 100, 1000.0, 'WAIT_REPORTING');
            """)

        migrated_mgr = DataManager(db_path=legacy_db, state_dir=self._tmp)
        self.assertIsNotNone(migrated_mgr)

        with migrated_mgr._get_conn() as conn:
            trade_type = conn.execute("SELECT typeof(xt_order_id) FROM trades WHERE trade_id='T-LEG'").fetchone()[0]
            order_type = conn.execute("SELECT typeof(xt_order_id) FROM orders WHERE order_uuid='uuid-leg'").fetchone()[0]
            trade_value = conn.execute("SELECT xt_order_id FROM trades WHERE trade_id='T-LEG'").fetchone()[0]
            order_value = conn.execute("SELECT xt_order_id FROM orders WHERE order_uuid='uuid-leg'").fetchone()[0]

        self.assertEqual(trade_type, "integer")
        self.assertEqual(order_type, "integer")
        self.assertEqual(trade_value, 1001)
        self.assertEqual(order_value, 1001)

    def test_trade_fee_columns_are_migrated(self):
        with self.mgr._get_conn() as conn:
            rows = conn.execute("PRAGMA table_info(trades)").fetchall()
        columns = {row[1] for row in rows}
        self.assertIn("buy_commission", columns)
        self.assertIn("sell_commission", columns)
        self.assertIn("stamp_tax", columns)
        self.assertIn("total_fee", columns)
        self.assertIn("is_t0", columns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
