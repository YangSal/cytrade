"""交易日工具。

统一管理交易日判断与交易日偏移计算，供策略与数据模块复用。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Iterable, Union

import chinese_calendar


DateLike = Union[str, date, datetime]

_DATE_FORMATS: tuple[str, ...] = (
    "%Y%m%d",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
)


def _coerce_to_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("日期字符串不能为空")

        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue

        normalized = text.replace("/", "-").replace(".", "-")
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError as exc:
            raise ValueError(f"不支持的日期格式: {value}") from exc

    raise TypeError(f"不支持的日期类型: {type(value)!r}")


def _format_date(value: DateLike) -> str:
    return _coerce_to_date(value).strftime("%Y%m%d")


@lru_cache(maxsize=4096)
def _is_market_day_cached(target: date) -> bool:
    return target.isoweekday() <= 5 and chinese_calendar.is_workday(target)


def is_market_day(value: DateLike) -> bool:
    """判断是否为交易日。"""
    return _is_market_day_cached(_coerce_to_date(value))


def shift_market_day(ref_date: DateLike, offset: int) -> str:
    """锚定日期偏移若干个交易日，返回 ``YYYYMMDD``。"""
    if not isinstance(offset, int):
        raise TypeError("offset 必须为 int")

    current = _coerce_to_date(ref_date)
    if offset == 0:
        return current.strftime("%Y%m%d")

    step = 1 if offset > 0 else -1
    remaining = abs(offset)
    while remaining > 0:
        current += timedelta(days=step)
        if is_market_day(current):
            remaining -= 1
    return current.strftime("%Y%m%d")


def add_one_market_day(ref_date: DateLike) -> str:
    """返回下一个交易日。"""
    return shift_market_day(ref_date, 1)


def minus_one_market_day(ref_date: DateLike) -> str:
    """返回上一个交易日。"""
    return shift_market_day(ref_date, -1)


def add_market_day(ref_date: DateLike, n: int) -> str:
    """锚定日期增加 ``n`` 个交易日。"""
    return shift_market_day(ref_date, n)


def add_mark_day(ref_date: DateLike, n: int) -> str:
    """兼容旧接口，等价于 ``add_market_day()``。"""
    return add_market_day(ref_date, n)


def date_range(date_start: DateLike, date_end: DateLike) -> list[str]:
    """返回闭区间内全部交易日，格式为 ``YYYYMMDD``。"""
    start = _coerce_to_date(date_start)
    end = _coerce_to_date(date_end)
    if start > end:
        return []

    days: list[str] = []
    current = start
    while current <= end:
        if is_market_day(current):
            days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


@dataclass
class TargetDate:
    """带锚定日期的交易日辅助对象。"""

    _ref_date: date

    def __init__(self, ref_date: DateLike):
        self._ref_date = _coerce_to_date(ref_date)

    @property
    def ref_date(self) -> str:
        return self._ref_date.strftime("%Y%m%d")

    def set_ref_date(self, ref_date: DateLike) -> None:
        self._ref_date = _coerce_to_date(ref_date)

    @property
    def is_market_day(self) -> bool:
        return is_market_day(self._ref_date)

    @staticmethod
    def to_date(date_str_: DateLike) -> date:
        return _coerce_to_date(date_str_)

    def add_mark_day(self, n: int) -> str:
        return add_market_day(self._ref_date, n)

    def add_market_day(self, n: int) -> str:
        return add_market_day(self._ref_date, n)


__all__ = [
    "TargetDate",
    "add_mark_day",
    "add_market_day",
    "add_one_market_day",
    "date_range",
    "is_market_day",
    "minus_one_market_day",
    "shift_market_day",
]