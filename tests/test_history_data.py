"""历史数据模块测试。"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.history_data import HistoryDataManager


class _FakeTqdm:
    def __init__(self, total=0, desc="", unit=""):
        self.total = total
        self.desc = desc
        self.unit = unit
        self.updated = 0
        self.postfix = ""
        self.closed = False

    def update(self, delta):
        self.updated += delta

    def set_postfix_str(self, text):
        self.postfix = text

    def close(self):
        self.closed = True


class TestHistoryDataManager(unittest.TestCase):

    def test_download_history_data_uses_batch_api_and_progress(self):
        fake_xtdata = MagicMock()

        def _download(stock_list, period, start_time='', end_time='', callback=None, incrementally=None):
            callback({"finished": 1, "total": 2, "stockcode": "000001.SZ", "message": ""})
            callback({"finished": 2, "total": 2, "stockcode": "000002.SZ", "message": "done"})

        fake_xtdata.download_history_data2.side_effect = _download
        fake_tqdm = _FakeTqdm()
        callback = MagicMock()
        mgr = HistoryDataManager()

        with patch('core.history_data._XT_AVAILABLE', True), \
             patch('core.history_data.xtdata', fake_xtdata), \
             patch('core.history_data._TQDM_AVAILABLE', True), \
             patch('core.history_data.tqdm', side_effect=lambda **kwargs: fake_tqdm):
            ok = mgr.download_history_data(["000001", "000002"], "20260101", "20260131", period="1d", callback=callback)

        self.assertTrue(ok)
        fake_xtdata.download_history_data2.assert_called_once()
        args = fake_xtdata.download_history_data2.call_args.args
        self.assertEqual(args[0], ["000001.SZ", "000002.SZ"])
        self.assertEqual(args[1], "1d")
        self.assertEqual(fake_tqdm.updated, 2)
        self.assertTrue(fake_tqdm.closed)
        self.assertEqual(callback.call_count, 2)

    def test_read_history_data_passes_field_list_and_fill_data(self):
        fake_xtdata = MagicMock()
        fake_xtdata.get_market_data_ex.return_value = {
            "000001.SZ": pd.DataFrame({"close": [10.0, 10.2]}),
        }
        mgr = HistoryDataManager()

        with patch('core.history_data._XT_AVAILABLE', True), \
             patch('core.history_data.xtdata', fake_xtdata):
            result = mgr.read_history_data(
                ["000001"],
                "20260101",
                "20260131",
                period="1d",
                dividend_type="front",
                field_list=["close"],
                fill_data=False,
            )

        fake_xtdata.get_market_data_ex.assert_called_once_with(
            field_list=["close"],
            stock_list=["000001.SZ"],
            period="1d",
            start_time="20260101",
            end_time="20260131",
            dividend_type="front",
            fill_data=False,
        )
        self.assertIn("000001", result)
        self.assertFalse(result["000001"].empty)

    def test_get_history_data_keeps_compatibility(self):
        mgr = HistoryDataManager()
        mgr.download_history_data = MagicMock(return_value=True)
        mgr.read_history_data = MagicMock(return_value={"000001": pd.DataFrame()})

        result = mgr.get_history_data(["000001"], "20260101", "20260131", field_list=["close"], fill_data=False)

        mgr.download_history_data.assert_called_once()
        mgr.read_history_data.assert_called_once()
        self.assertIn("000001", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)