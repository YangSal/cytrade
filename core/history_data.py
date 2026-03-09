"""历史数据模块。

提供批量历史数据下载与独立读取能力：
- 下载：xtdata.download_history_data2
- 读取：xtdata.get_market_data_ex
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import pandas as pd

from monitor.logger import get_logger

logger = get_logger("system")

try:
    from tqdm.auto import tqdm
    _TQDM_AVAILABLE = True
except ImportError:
    tqdm = None  # type: ignore
    _TQDM_AVAILABLE = False

try:
    from xtquant import xtdata
    _XT_AVAILABLE = True
except ImportError:
    _XT_AVAILABLE = False
    xtdata = None  # type: ignore


class HistoryDataManager:
    """历史行情数据获取"""

    # 股票代码转换规则
    _SH_PREFIXES = ("6", "5")   # 上海：6开头（主板）、5开头（ETF）
    _SZ_PREFIXES = ("0", "3")   # 深圳：0开头（主板）、3开头（创业板）

    def download_history_data(
        self,
        stock_list: List[str],
        start_date: str = "",
        end_date: str = "",
        period: str = "1d",
        callback: Optional[Callable[[dict], None]] = None,
        incrementally=None,
        show_progress: bool = True,
    ) -> bool:
        """批量下载历史行情到本地缓存。"""
        if not stock_list:
            return True

        xt_codes = [self.stock_code_to_xt(c) for c in stock_list]

        if not _XT_AVAILABLE:
            logger.warning("HistoryDataManager: xtquant 未安装，跳过历史数据下载")
            return False

        progress = None
        if show_progress and _TQDM_AVAILABLE:
            progress = tqdm(total=len(xt_codes), desc=f"下载历史数据[{period}]", unit="code")

        state = {"finished": 0}

        def _on_progress(data: dict) -> None:
            finished = int(data.get("finished", 0) or 0)
            delta = max(0, finished - state["finished"])
            state["finished"] = finished
            if progress is not None and delta > 0:
                progress.update(delta)
            if progress is not None:
                stockcode = data.get("stockcode", "")
                message = data.get("message", "")
                progress.set_postfix_str(f"{stockcode} {message}".strip())
            if callback:
                callback(data)

        try:
            xtdata.download_history_data2(
                xt_codes,
                period,
                start_time=start_date,
                end_time=end_date,
                callback=_on_progress,
                incrementally=incrementally,
            )
            logger.info(
                "HistoryDataManager: %d 只股票历史数据下载完成 (%s~%s %s)",
                len(xt_codes), start_date or "-", end_date or "-", period,
            )
            return True
        except Exception as e:
            logger.error("HistoryDataManager: 下载数据失败: %s", e, exc_info=True)
            return False
        finally:
            if progress is not None:
                progress.close()

    def read_history_data(
        self,
        stock_list: List[str],
        start_date: str,
        end_date: str,
        period: str = "1d",
        dividend_type: str = "front",
        field_list: Optional[List[str]] = None,
        fill_data: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        """从本地缓存读取历史行情。"""
        if not stock_list:
            return {}

        xt_codes = [self.stock_code_to_xt(c) for c in stock_list]

        if not _XT_AVAILABLE:
            logger.warning("HistoryDataManager: xtquant 未安装，返回空数据")
            return {c: pd.DataFrame() for c in stock_list}

        try:
            raw: dict = xtdata.get_market_data_ex(
                field_list=field_list or [],
                stock_list=xt_codes,
                period=period,
                start_time=start_date,
                end_time=end_date,
                dividend_type=dividend_type,
                fill_data=fill_data,
            )

            result: Dict[str, pd.DataFrame] = {c: pd.DataFrame() for c in stock_list}
            for xt_code, df in raw.items():
                code = self.xt_code_to_stock(xt_code)
                result[code] = df if isinstance(df, pd.DataFrame) and not df.empty else pd.DataFrame()

            logger.info(
                "HistoryDataManager: %d 只股票历史数据已读取 (%s~%s %s %s)",
                len(result), start_date, end_date, period, dividend_type
            )
            return result

        except Exception as e:
            logger.error("HistoryDataManager: 读取数据失败: %s", e, exc_info=True)
            return {c: pd.DataFrame() for c in stock_list}

    def get_history_data(
        self,
        stock_list: List[str],
        start_date: str,
        end_date: str,
        period: str = "1d",
        dividend_type: str = "front",
        field_list: Optional[List[str]] = None,
        fill_data: bool = True,
        callback: Optional[Callable[[dict], None]] = None,
        incrementally=None,
        show_progress: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        """兼容接口：先下载，再读取历史行情。"""
        ok = self.download_history_data(
            stock_list=stock_list,
            start_date=start_date,
            end_date=end_date,
            period=period,
            callback=callback,
            incrementally=incrementally,
            show_progress=show_progress,
        )
        if not ok and not _XT_AVAILABLE:
            return {c: pd.DataFrame() for c in stock_list}
        return self.read_history_data(
            stock_list=stock_list,
            start_date=start_date,
            end_date=end_date,
            period=period,
            dividend_type=dividend_type,
            field_list=field_list,
            fill_data=fill_data,
        )

    @classmethod
    def stock_code_to_xt(cls, code: str) -> str:
        """6位数字代码 → xtquant 格式（含后缀）"""
        code = str(code).strip().zfill(6)
        if code.startswith(cls._SH_PREFIXES):
            return f"{code}.SH"
        return f"{code}.SZ"

    @classmethod
    def xt_code_to_stock(cls, xt_code: str) -> str:
        """xtquant 格式 → 6位数字代码"""
        return xt_code.split(".")[0] if "." in xt_code else xt_code


__all__ = ["HistoryDataManager"]
