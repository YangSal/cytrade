"""费率表加载与手续费计算。

这个模块解决两个问题：
1. 从 CSV 文件读取不同证券的费率规则。
2. 在成交金额已知的前提下，算出佣金、印花税和总费用。

设计目标是让上层模块不需要关心“这只证券到底用哪条费率规则”，
只要传入证券代码、方向和成交金额，就能拿到统一的费用拆分结果。
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from fnmatch import fnmatchcase


logger = logging.getLogger(__name__)


def _to_bool(value) -> bool:
    """把各种文本形式的开关值转换成布尔值。

    例如：``1``、``true``、``yes`` 都会被视为 ``True``。
    这样在读取 CSV 或环境变量时，用户可以使用较宽松的写法。
    """
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _to_float(value, default: float = 0.0) -> float:
    """安全地把输入值转成浮点数。

    读取配置文件时经常会遇到空字符串、``None`` 或非法文本。
    这里统一兜底，避免在加载整张费率表时因为一行脏数据直接抛异常。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_fee_up(value: float) -> float:
    """按“分”向上取整手续费。

    交易费用通常不能无限保留小数位，实际记账时往往按分处理。
    这里使用 ``ROUND_CEILING``，即只要有零头就向上补到下一分，
    用来模拟较常见的券商计费方式。
    """
    if value <= 0:
        return 0.0
    # 先转成“分”，再向上取整，最后再除以 100 还原成“元”。
    cents = (Decimal(str(value)) * Decimal("100")).to_integral_value(rounding=ROUND_CEILING)
    return float(cents / Decimal("100"))


@dataclass(frozen=True)
class FeeRule:
    """单条费率规则。

    一条规则描述“哪些证券代码匹配这条规则”以及“匹配后该用什么费率”。
    例如：
    - ``600***`` 代表一批证券
    - ``510300`` 代表单只证券
    - ``*`` 代表默认兜底规则
    """
    code_pattern: str
    buy_fee_rate: float
    sell_fee_rate: float
    stamp_tax_rate: float
    is_t0: bool = False
    description: str = ""

    def matches(self, stock_code: str) -> bool:
        """判断某个证券代码是否命中当前规则。

        匹配顺序说明：
        1. 空代码直接不匹配。
        2. ``*`` / ``ALL`` / ``DEFAULT`` 视为全匹配。
        3. 含通配符时使用 ``fnmatchcase`` 做模式匹配。
        4. 否则按完全相等匹配。
        """
        code = str(stock_code or "").strip()
        pattern = (self.code_pattern or "*").strip()
        if not code:
            return False
        if pattern in {"*", "ALL", "DEFAULT"}:
            return True
        if "*" in pattern or "?" in pattern:
            # 例如 ``51*``、``159???`` 这类模式走通配符匹配。
            return fnmatchcase(code, pattern)
        return code == pattern

    @property
    def specificity(self) -> tuple[int, int]:
        """返回规则“具体程度”，用于多条命中时选最精确的一条。

        返回值规则：
        - 有效字符越多，越具体。
        - 通配符越少，越具体。
        """
        pattern = (self.code_pattern or "*").strip()
        wildcards = pattern.count("*") + pattern.count("?")
        return (len(pattern) - wildcards, -wildcards)


@dataclass(frozen=True)
class SecurityFeeProfile:
    """某只证券最终生效的费率画像。

    这是规则匹配后的“结果对象”，上层只使用这个对象，
    不需要再关心它到底来自哪一条 CSV 规则。
    """
    stock_code: str
    buy_fee_rate: float
    sell_fee_rate: float
    stamp_tax_rate: float
    is_t0: bool = False
    source: str = "default"


@dataclass(frozen=True)
class FeeBreakdown:
    """一次成交对应的费用拆分结果。"""
    buy_commission: float = 0.0
    sell_commission: float = 0.0
    stamp_tax: float = 0.0
    total_fee: float = 0.0
    is_t0: bool = False


class FeeSchedule:
    """按证券代码匹配费率表，并计算费用。

    使用流程通常是：
    1. 初始化时加载费率表。
    2. 成交发生时，根据证券代码匹配到最终费率画像。
    3. 按买卖方向计算佣金和印花税。
    """

    def __init__(self, file_path: str = "", default_buy_fee_rate: float = 0.0001,
                 default_sell_fee_rate: float = 0.0001, default_stamp_tax_rate: float = 0.0003):
        """创建费率表对象并立即加载规则。

        如果文件不存在或读取失败，不抛异常，而是退回默认费率，
        这样系统仍可继续运行。
        """
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
        """返回当前费率表文件路径。"""
        return self._file_path

    def get_profile(self, stock_code: str) -> SecurityFeeProfile:
        """获取某只证券最终生效的费率配置。

        这里会先把证券代码标准化成 6 位，再找出所有命中的规则。
        如果命中多条规则，则按 ``specificity`` 选择最具体的一条。
        如果一条都没有命中，则使用默认费率。
        """
        code = str(stock_code or "").strip().zfill(6)
        if not code:
            return self._default_profile

        matches = [rule for rule in self._rules if rule.matches(code)]
        if not matches:
            # 没有命中任何自定义规则时，返回默认费率画像。
            return SecurityFeeProfile(
                stock_code=code,
                buy_fee_rate=self._default_profile.buy_fee_rate,
                sell_fee_rate=self._default_profile.sell_fee_rate,
                stamp_tax_rate=self._default_profile.stamp_tax_rate,
                is_t0=self._default_profile.is_t0,
                source="default",
            )

    # 多条命中时，选择“最具体”的规则，避免宽泛规则覆盖精确规则。
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
        """判断某只证券是否支持当日回转（T+0）。"""
        return self.get_profile(stock_code).is_t0

    def calculate(self, stock_code: str, direction, amount: float) -> FeeBreakdown:
        """根据证券、方向和成交金额计算费用。

        参数说明：
        - ``stock_code``：证券代码，用于匹配费率。
        - ``direction``：买卖方向，既支持枚举，也支持纯字符串。
        - ``amount``：成交金额，单位为元。

        计算规则：
        - 买入：只收买入佣金。
        - 卖出：收卖出佣金 + 印花税。
        """
        profile = self.get_profile(stock_code)
        gross_amount = float(amount or 0.0)
        direction_value = getattr(direction, "value", direction)
        direction_text = str(direction_value or "").upper()

        buy_commission = 0.0
        sell_commission = 0.0
        stamp_tax = 0.0

        if gross_amount > 0:
            if direction_text == "BUY":
                # 买入通常只计算买入佣金。
                buy_commission = _round_fee_up(gross_amount * profile.buy_fee_rate)
            else:
                # 卖出通常同时计算卖出佣金和印花税。
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
        """从 CSV 文件加载费率规则列表。

        文件支持以下特性：
        - UTF-8 with BOM
        - 空行自动跳过
        - ``#`` 开头的注释行自动跳过
        """
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
                        # 没有代码模式的行无法参与匹配，直接忽略。
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