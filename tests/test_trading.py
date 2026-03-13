"""
交易模块测试
独立测试各种交易方式（Mock 模式，无需真实 QMT 连接）
运行方式: python -m pytest tests/test_trading.py -v
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import MagicMock, patch

from trading.executor import TradeExecutor
from trading.order_manager import OrderManager
from trading.models import Order
from config.enums import OrderStatus, OrderDirection, OrderType


def _make_executor():
    """创建无真实连接的 TradeExecutor"""
    data_mgr = MagicMock()
    order_mgr = OrderManager(data_manager=data_mgr)
    conn_mgr = MagicMock()
    conn_mgr.get_trader.return_value = None   # 触发 Mock 下单路径
    conn_mgr.is_connected.return_value = True
    pos_mgr = MagicMock()
    pos_mgr.get_position.return_value = MagicMock(available_quantity=1000)
    executor = TradeExecutor(conn_mgr, order_mgr, pos_mgr)
    return executor, order_mgr


class TestBuyLimit(unittest.TestCase):
    def test_buy_limit(self):
        """限价买入"""
        executor, order_mgr = _make_executor()
        order = executor.buy_limit("sid1", "TestStrategy", "600000", 10.50, 100,
                                   remark="test buy limit")
        self.assertIsInstance(order, Order)
        self.assertEqual(order.direction, OrderDirection.BUY)
        self.assertEqual(order.order_type, OrderType.LIMIT)
        self.assertEqual(order.price, 10.50)
        self.assertEqual(order.quantity, 100)
        self.assertIn(order.order_uuid, order_mgr._orders)

    def test_buy_market(self):
        """市价买入"""
        executor, _ = _make_executor()
        order = executor.buy_market("sid1", "TestStrategy", "600000", 200,
                                    remark="test market buy")
        self.assertEqual(order.order_type, OrderType.MARKET)
        self.assertEqual(order.quantity, 200)
        self.assertEqual(order.price, 0.0)

    def test_buy_by_amount(self):
        """按金额买入 — 自动取整"""
        executor, _ = _make_executor()
        # 10000 / 10.5 = 952 股 → 取整到 900 股
        order = executor.buy_by_amount("sid1", "TestStrategy", "600000",
                                       price=10.5, amount=10000,
                                       remark="test amount buy")
        self.assertEqual(order.order_type, OrderType.BY_AMOUNT)
        self.assertEqual(order.quantity % 100, 0)  # 必须是100的倍数

    def test_buy_by_amount_insufficient(self):
        """金额不足买1手"""
        executor, _ = _make_executor()
        order = executor.buy_by_amount("sid1", "TestStrategy", "000001",
                                       price=100.0, amount=50,
                                       remark="insufficient amount")
        self.assertEqual(order.status, OrderStatus.JUNK)

    def test_sell_limit(self):
        """限价卖出"""
        executor, _ = _make_executor()
        order = executor.sell_limit("sid1", "TestStrategy", "600000", 11.0, 100,
                                    remark="test sell limit")
        self.assertEqual(order.direction, OrderDirection.SELL)
        self.assertEqual(order.price, 11.0)

    def test_sell_market(self):
        """市价卖出"""
        executor, _ = _make_executor()
        order = executor.sell_market("sid1", "TestStrategy", "600000", 100,
                                     remark="test market sell")
        self.assertEqual(order.direction, OrderDirection.SELL)
        self.assertEqual(order.order_type, OrderType.MARKET)

    def test_close_position(self):
        """平仓"""
        executor, _ = _make_executor()
        order = executor.close_position("sid1", "TestStrategy", "600000",
                                        remark="test close")
        self.assertEqual(order.direction, OrderDirection.SELL)
        self.assertEqual(order.remark, "test close")

    def test_cancel_order(self):
        """撤单"""
        executor, order_mgr = _make_executor()
        order = executor.buy_limit("sid1", "TestStrategy", "600000", 10.0, 100,
                                   remark="to cancel")
        result = executor.cancel_order(order.order_uuid, remark="test cancel")
        self.assertTrue(result)

    def test_resolve_market_price_type_uses_available_fallback_for_sz(self):
        with patch("trading.executor.xtconstant") as fake_xtconstant:
            fake_xtconstant.FIX_PRICE = 11
            fake_xtconstant.MARKET_SZ_CONVERT = None
            fake_xtconstant.MARKET_SZ_INSTBUSI_RESTCANCEL = 47
            fake_xtconstant.MARKET_CONVERT_5 = 24

            value = TradeExecutor._resolve_market_price_type("159981")

        self.assertEqual(value, 47)

    def test_resolve_market_price_type_uses_available_fallback_for_sh(self):
        with patch("trading.executor.xtconstant") as fake_xtconstant:
            fake_xtconstant.FIX_PRICE = 11
            fake_xtconstant.MARKET_SH_INSTANT = None
            fake_xtconstant.MARKET_SH_CONVERT_5_LIMIT = 42
            fake_xtconstant.MARKET_CONVERT_5 = 24

            value = TradeExecutor._resolve_market_price_type("600000")

        self.assertEqual(value, 42)

    def test_order_tracking(self):
        """订单追踪 — uuid 查询"""
        executor, order_mgr = _make_executor()
        order = executor.buy_limit("sid1", "TestStrategy", "000001", 15.0, 200)
        found = order_mgr.get_order(order.order_uuid)
        self.assertIsNotNone(found)
        self.assertEqual(found.order_uuid, order.order_uuid)

    def test_partial_fill(self):
        """部分成交"""
        executor, order_mgr = _make_executor()
        order = executor.buy_limit("sid1", "TestStrategy", "000001", 15.0, 400)
        trade_info = {
            "trade_id": "T001",
            "xt_order_id": order.xt_order_id,
            "stock_code": "000001",
            "direction": "BUY",
            "price": 15.0,
            "quantity": 200,
            "amount": 3000.0,
            "commission": 0.0,
        }
        order_mgr._xt_to_uuid[order.xt_order_id] = order.order_uuid
        order_mgr.on_trade(order.xt_order_id, trade_info)
        updated = order_mgr.get_order(order.order_uuid)
        self.assertEqual(updated.filled_quantity, 200)
        self.assertEqual(updated.status, OrderStatus.PART_SUCC)

    def test_order_junk(self):
        """订单废单状态更新"""
        executor, order_mgr = _make_executor()
        order = executor.buy_limit("sid1", "TestStrategy", "000001", 15.0, 100)
        order_mgr._xt_to_uuid[order.xt_order_id] = order.order_uuid
        order_mgr.update_order_status(order.xt_order_id, OrderStatus.JUNK)
        updated = order_mgr.get_order(order.order_uuid)
        self.assertEqual(updated.status, OrderStatus.JUNK)

    def test_order_stock_async_uses_positional_strategy_and_remark(self):
        data_mgr = MagicMock()
        order_mgr = OrderManager(data_manager=data_mgr)
        conn_mgr = MagicMock()
        trader = MagicMock()
        trader.order_stock_async.return_value = 77
        conn_mgr.get_trader.return_value = trader
        conn_mgr.is_connected.return_value = True
        conn_mgr.account = MagicMock()
        pos_mgr = MagicMock()
        pos_mgr.get_position.return_value = MagicMock(available_quantity=1000)
        executor = TradeExecutor(conn_mgr, order_mgr, pos_mgr)

        with patch('trading.executor._XT_AVAILABLE', True):
            order = executor.buy_limit('sid1', 'TestStrategy', '600000', 10.5, 100, remark='remark-test')

        trader.order_stock_async.assert_called_once()
        args = trader.order_stock_async.call_args.args
        self.assertEqual(args[1], '600000.SH')
        self.assertEqual(args[2], 23)
        self.assertEqual(args[3], 100)
        self.assertEqual(args[5], 10.5)
        self.assertEqual(args[6], 'TestStrategy')
        self.assertEqual(args[7], 'remark-test')


if __name__ == "__main__":
    unittest.main(verbosity=2)
