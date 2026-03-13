"""
策略模块测试（网格策略 + 止盈止损）
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
import tempfile
from unittest.mock import MagicMock

from strategy.csv_signal_strategy import CsvSignalStrategy
from strategy.test_grid_strategy import TestGridStrategy
from strategy.models import StrategyConfig
from core.models import TickData
from config.enums import StrategyStatus


def _tick(code, price):
    return TickData(stock_code=code, last_price=price, open=price,
                    high=price, low=price, pre_close=price * 0.99,
                    volume=10000, amount=price * 10000,
                    bid_prices=[price - 0.01] * 5,
                    bid_volumes=[100] * 5,
                    ask_prices=[price + 0.01] * 5,
                    ask_volumes=[100] * 5)


def _make_strategy(stop_loss=0.0, take_profit=0.0):
    executor = MagicMock()
    pos_mgr = MagicMock()
    pos_mgr.get_position.return_value = MagicMock(
        total_quantity=1000, available_quantity=1000
    )

    def fake_buy_by_amount(sid, sname, code, price, amount, remark=""):
        from trading.models import Order
        from config.enums import OrderDirection, OrderType
        return Order(strategy_id=sid, strategy_name=sname, stock_code=code,
                     direction=OrderDirection.BUY, order_type=OrderType.BY_AMOUNT,
                     price=price, quantity=int(amount / price / 100) * 100,
                     amount=amount, remark=remark)

    def fake_close(sid, sname, code, remark=""):
        from trading.models import Order
        from config.enums import OrderDirection, OrderType
        return Order(strategy_id=sid, strategy_name=sname, stock_code=code,
                     direction=OrderDirection.SELL, order_type=OrderType.MARKET,
                     remark=remark)

    executor.buy_by_amount.side_effect = fake_buy_by_amount
    executor.buy_limit.return_value = MagicMock(order_uuid="uuid-buy")
    executor.close_position.side_effect = fake_close
    executor.sell_limit.return_value = MagicMock(order_uuid="uuid-sell")

    cfg = StrategyConfig(
        stock_code="000001",
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        params={
            "grid_count": 5,
            "grid_low": 9.5,
            "grid_high": 10.5,
            "per_grid_amount": 5000.0,
        }
    )
    s = TestGridStrategy(cfg, executor, pos_mgr)
    s.start()
    return s, executor, pos_mgr


class TestGridStrategyUnit(unittest.TestCase):

    def test_initialization(self):
        s, _, _ = _make_strategy()
        self.assertEqual(s.status, StrategyStatus.RUNNING)
        self.assertEqual(s.stock_code, "000001")

    def test_first_tick_initializes_grid(self):
        s, executor, _ = _make_strategy()
        s.process_tick(_tick("000001", 10.0))
        # 第一个 tick 初始化网格，不产生信号
        executor.buy_by_amount.assert_not_called()
        self.assertTrue(s._initialized)
        self.assertEqual(len(s._grid_levels), 6)  # grid_count=5 → 6个档位

    def test_buy_signal_on_down_cross(self):
        """价格下穿网格线 → 买入信号"""
        s, executor, _ = _make_strategy()
        s.process_tick(_tick("000001", 10.0))        # 初始化，_last_price=10.0
        level = s._grid_levels[2]                    # 取第3个档
        # 推送价格略低于该档位（从高到低跨过 level）
        s._last_price = level + 0.1
        s.process_tick(_tick("000001", level - 0.01))
        executor.buy_by_amount.assert_called()

    def test_sell_signal_on_up_cross(self):
        """价格上穿网格线 → 卖出信号"""
        s, executor, _ = _make_strategy()
        s.process_tick(_tick("000001", 10.0))
        level = s._grid_levels[3]
        s._last_price = level - 0.1
        s.process_tick(_tick("000001", level + 0.01))
        executor.sell_limit.assert_called()

    def test_fixed_grid_quantity_uses_one_lot(self):
        """配置固定网格股数时，买卖均按指定手数执行。"""
        s, executor, pos_mgr = _make_strategy()
        s.config.params["per_grid_quantity"] = 100
        s._per_grid_quantity = 100

        s.process_tick(_tick("000001", 10.0))

        buy_level = s._grid_levels[2]
        s._last_price = buy_level + 0.1
        s.process_tick(_tick("000001", buy_level - 0.01))
        executor.buy_limit.assert_called()
        buy_args, _ = executor.buy_limit.call_args
        self.assertEqual(buy_args[4], 100)

        pos_mgr.get_position.return_value = MagicMock(total_quantity=1000, available_quantity=1000)
        sell_level = s._grid_levels[3]
        s._last_price = sell_level - 0.1
        s.process_tick(_tick("000001", sell_level + 0.01))
        sell_args, _ = executor.sell_limit.call_args
        self.assertEqual(sell_args[4], 100)

    def test_stop_loss_triggers_close(self):
        """止损触发平仓"""
        s, executor, _ = _make_strategy(stop_loss=9.0)
        s.process_tick(_tick("000001", 10.0))       # 初始化
        s.process_tick(_tick("000001", 8.5))        # 低于止损价
        executor.close_position.assert_called()

    def test_take_profit_triggers_close(self):
        """止盈触发平仓"""
        s, executor, _ = _make_strategy(take_profit=12.0)
        s.process_tick(_tick("000001", 10.0))       # 初始化
        s.process_tick(_tick("000001", 12.5))       # 高于止盈价
        executor.close_position.assert_called()

    def test_pause_stops_processing(self):
        """暂停后不处理 tick"""
        s, executor, _ = _make_strategy()
        s.process_tick(_tick("000001", 10.0))
        s.pause()
        executor.buy_by_amount.reset_mock()
        level = s._grid_levels[2]
        s._last_price = level + 0.1
        s.process_tick(_tick("000001", level - 0.01))
        executor.buy_by_amount.assert_not_called()

    def test_snapshot_restore(self):
        """快照保存与恢复"""
        s, _, _ = _make_strategy()
        s.process_tick(_tick("000001", 10.0))
        snap = s.get_snapshot()
        self.assertEqual(snap.stock_code, "000001")
        self.assertEqual(snap.strategy_name, "TestGrid")

        # 恢复
        from strategy.models import StrategyConfig
        cfg2 = StrategyConfig(stock_code="000001", params={
            "grid_count": 5, "grid_low": 9.5, "grid_high": 10.5, "per_grid_amount": 5000
        })
        s2 = TestGridStrategy(cfg2)
        s2.restore_from_snapshot(snap)
        self.assertEqual(s2.strategy_id, s.strategy_id)
        self.assertTrue(s2._initialized)


class TestCsvSignalStrategy(unittest.TestCase):

    def test_select_stocks_loads_csv_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "signals.csv")
            with open(csv_path, "w", encoding="utf-8-sig") as fp:
                fp.write("股票代码,开仓价格,买入数量,止损位（百分比）,止盈位（百分比）\n")
                fp.write("000001,10.00,1000,3,6\n")
                fp.write("600000.SH,8.50,2000,2.5%,5%\n")

            strategy = CsvSignalStrategy(StrategyConfig(params={"csv_path": csv_path}))
            configs = strategy.select_stocks()

            self.assertEqual(len(configs), 2)
            self.assertEqual(configs[0].stock_code, "000001")
            self.assertEqual(configs[0].stop_loss_price, 9.7)
            self.assertEqual(configs[0].take_profit_price, 10.6)
            self.assertEqual(configs[1].stock_code, "600000")
            self.assertEqual(configs[1].params["buy_quantity"], 2000)

    def test_on_tick_returns_buy_signal_when_price_reaches_entry(self):
        pos_mgr = MagicMock()
        pos_mgr.get_position.return_value = None
        strategy = CsvSignalStrategy(
            StrategyConfig(
                stock_code="000001",
                entry_price=10.0,
                params={"buy_quantity": 1200},
            ),
            trade_executor=MagicMock(),
            position_manager=pos_mgr,
        )
        strategy.start()

        signal = strategy.on_tick(_tick("000001", 9.98))

        self.assertIsNotNone(signal)
        self.assertEqual(signal["action"], "BUY")
        self.assertEqual(signal["quantity"], 1200)

    def test_on_tick_skips_when_position_exists(self):
        pos_mgr = MagicMock()
        pos_mgr.get_position.return_value = MagicMock(total_quantity=100)
        strategy = CsvSignalStrategy(
            StrategyConfig(
                stock_code="000001",
                entry_price=10.0,
                params={"buy_quantity": 1000},
            ),
            trade_executor=MagicMock(),
            position_manager=pos_mgr,
        )
        strategy.start()

        signal = strategy.on_tick(_tick("000001", 9.95))

        self.assertIsNone(signal)


if __name__ == "__main__":
    unittest.main(verbosity=2)
