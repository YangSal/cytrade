"""费率表加载与手续费计算。"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from fnmatch import fnmatchcase


logger = logging.getLogger(__name__)


def _to_bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_fee_up(value: float) -> float:
    if value <= 0:
        return 0.0
    cents = (Decimal(str(value)) * Decimal("100")).to_integral_value(rounding=ROUND_CEILING)
    return float(cents / Decimal("100"))


@dataclass(frozen=True)
class FeeRule:
    code_pattern: str
    buy_fee_rate: float
    sell_fee_rate: float
    stamp_tax_rate: float
    is_t0: bool = False
    description: str = ""

    def matches(self, stock_code: str) -> bool:
        code = str(stock_code or "").strip()
        pattern = (self.code_pattern or "*").strip()
        if not code:
            return False
        if pattern in {"*", "ALL", "DEFAULT"}:
            return True
        if "*" in pattern or "?" in pattern:
            return fnmatchcase(code, pattern)
        return code == pattern

    @property
    def specificity(self) -> tuple[int, int]:
        pattern = (self.code_pattern or "*").strip()
        wildcards = pattern.count("*") + pattern.count("?")
        return (len(pattern) - wildcards, -wildcards)


@dataclass(frozen=True)
class SecurityFeeProfile:
    stock_code: str
    buy_fee_rate: float
    sell_fee_rate: float
    stamp_tax_rate: float
    is_t0: bool = False
    source: str = "default"


@dataclass(frozen=True)
class FeeBreakdown:
    buy_commission: float = 0.0
    sell_commission: float = 0.0
    stamp_tax: float = 0.0
    total_fee: float = 0.0
    is_t0: bool = False


class FeeSchedule:
    """按证券代码匹配费率表，并计算费用。"""

    def __init__(self, file_path: str = "", default_buy_fee_rate: float = 0.0001,
                 default_sell_fee_rate: float = 0.0001, default_stamp_tax_rate: float = 0.0003):
        self._file_path = file_path or ""
        self._default_profile = SecurityFeeProfile(
            stock_code="*",
            buy_fee_rate=float(default_buy_fee_rate),
            sell_fee_rate=float(default_sell_fee_rate),
            stamp_tax_rate=float(default_stamp_tax_rate),
            is_t0=False,
            source="default",
        )
        self._rules = self._load_rules(self._file_path)

    @property
    def file_path(self) -> str:
        return self._file_path

    def get_profile(self, stock_code: str) -> SecurityFeeProfile:
        code = str(stock_code or "").strip().zfill(6)
        if not code:
            return self._default_profile

        matches = [rule for rule in self._rules if rule.matches(code)]
        if not matches:
            return SecurityFeeProfile(
                stock_code=code,
                buy_fee_rate=self._default_profile.buy_fee_rate,
                sell_fee_rate=self._default_profile.sell_fee_rate,
                stamp_tax_rate=self._default_profile.stamp_tax_rate,
                is_t0=self._default_profile.is_t0,
                source="default",
            )

        rule = sorted(matches, key=lambda item: item.specificity, reverse=True)[0]
        return SecurityFeeProfile(
            stock_code=code,
            buy_fee_rate=rule.buy_fee_rate,
            sell_fee_rate=rule.sell_fee_rate,
            stamp_tax_rate=rule.stamp_tax_rate,
            is_t0=rule.is_t0,
            source=rule.code_pattern,
        )

    def is_t0_security(self, stock_code: str) -> bool:
        return self.get_profile(stock_code).is_t0

    def calculate(self, stock_code: str, direction, amount: float) -> FeeBreakdown:
        profile = self.get_profile(stock_code)
        gross_amount = float(amount or 0.0)
        direction_value = getattr(direction, "value", direction)
        direction_text = str(direction_value or "").upper()

        buy_commission = 0.0
        sell_commission = 0.0
        stamp_tax = 0.0

        if gross_amount > 0:
            if direction_text == "BUY":
                buy_commission = _round_fee_up(gross_amount * profile.buy_fee_rate)
            else:
                sell_commission = _round_fee_up(gross_amount * profile.sell_fee_rate)
                stamp_tax = _round_fee_up(gross_amount * profile.stamp_tax_rate)

        total_fee = buy_commission + sell_commission + stamp_tax
        return FeeBreakdown(
            buy_commission=buy_commission,
            sell_commission=sell_commission,
            stamp_tax=stamp_tax,
            total_fee=total_fee,
            is_t0=profile.is_t0,
        )

    @staticmethod
    def _load_rules(file_path: str) -> list[FeeRule]:
        if not file_path:
            return []
        try:
            with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(
                    row for row in f
                    if row.strip() and not row.lstrip().startswith("#")
                )
                rules: list[FeeRule] = []
                for row in reader:
                    pattern = str(row.get("code_pattern", "") or "").strip()
                    if not pattern:
                        continue
                    rules.append(FeeRule(
                        code_pattern=pattern,
                        buy_fee_rate=_to_float(row.get("buy_fee_rate"), 0.0),
                        sell_fee_rate=_to_float(row.get("sell_fee_rate"), 0.0),
                        stamp_tax_rate=_to_float(row.get("stamp_tax_rate"), 0.0),
                        is_t0=_to_bool(row.get("is_t0")),
                        description=str(row.get("description", "") or "").strip(),
                    ))
                return rules
        except FileNotFoundError:
            logger.info("FeeSchedule: fee table not found, use defaults: %s", file_path)
            return []
        except Exception as exc:
            logger.warning("FeeSchedule: failed to load %s: %s", file_path, exc)
            return []


__all__ = ["FeeBreakdown", "FeeRule", "FeeSchedule", "SecurityFeeProfile"]