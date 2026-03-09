"""
数据订阅模块
- 订阅/取消订阅实时行情
- 预处理 xtquant 推送数据 → TickData
- 计算数据延迟并记录到日志
- 通过回调传递给策略运行模块
"""
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from config.enums import SubscriptionPeriod
from core.models import TickData
from monitor.logger import get_logger

logger = get_logger("system")

try:
    from xtquant import xtdata
    _XT_AVAILABLE = True
except ImportError:
    _XT_AVAILABLE = False
    xtdata = None  # type: ignore


class DataSubscriptionManager:
    """实时行情数据订阅管理"""

    def __init__(self, latency_threshold_sec: float = 10.0,
                 default_period: SubscriptionPeriod | str = SubscriptionPeriod.TICK):
        self._subscriptions: Dict[str, str] = {}  # {stock_code: period}
        self._subscription_ids: Dict[str, int] = {}  # {stock_code: subscribe_id}
        self._data_callback: Optional[Callable[[Dict[str, TickData]], None]] = None
        self._latency_threshold = latency_threshold_sec
        self._default_period = self._normalize_period(default_period)
        self._running = False
        self._whole_market = False
        self._whole_market_subscribe_id: Optional[int] = None
        self._lock = threading.Lock()
        self._last_recv_time: Optional[datetime] = None

    # ------------------------------------------------------------------ Public

    def subscribe_stocks(self, stock_codes: List[str], period: SubscriptionPeriod | str = "") -> None:
        """订阅股票列表行情"""
        period = self._normalize_period(period or self._default_period)
        xt_codes = [self._to_xt(c) for c in stock_codes]
        with self._lock:
            for code, xt_code in zip(stock_codes, xt_codes):
                self._subscriptions[code] = period

        if not _XT_AVAILABLE:
            logger.warning("DataSubscription: xtquant 未安装，跳过实际订阅")
            return
        try:
            # 逐只订阅（xtdata.subscribe_quote 接收单个代码）
            for code, xt_code in zip(stock_codes, xt_codes):
                old_sub_id = self._subscription_ids.get(code)
                if old_sub_id is not None:
                    try:
                        xtdata.unsubscribe_quote(old_sub_id)
                    except Exception:
                        pass
                sub_id = xtdata.subscribe_quote(
                    xt_code,
                    period=period,
                    count=-1,
                    callback=self._on_data,
                )
                self._subscription_ids[code] = int(sub_id) if sub_id is not None else -1
            logger.info("DataSubscription: 订阅 %d 只股票 [%s]", len(xt_codes), period)
        except Exception as e:
            logger.error("DataSubscription: 订阅失败: %s", e, exc_info=True)

    def unsubscribe_stocks(self, stock_codes: List[str]) -> None:
        """取消订阅"""
        xt_codes = [self._to_xt(c) for c in stock_codes]
        sub_ids = {}
        with self._lock:
            for code in stock_codes:
                sub_ids[code] = self._subscription_ids.get(code)
                self._subscriptions.pop(code, None)
                self._subscription_ids.pop(code, None)
        if not _XT_AVAILABLE:
            return
        try:
            for code in stock_codes:
                sub_id = sub_ids.get(code)
                if sub_id is not None:
                    xtdata.unsubscribe_quote(sub_id)
            logger.info("DataSubscription: 取消订阅 %d 只股票", len(xt_codes))
        except Exception as e:
            logger.error("DataSubscription: 取消订阅失败: %s", e, exc_info=True)

    def subscribe_whole_market(self, period: SubscriptionPeriod | str = "") -> None:
        """全市场订阅"""
        period = self._normalize_period(period or self._default_period)
        self._whole_market = True
        if not _XT_AVAILABLE:
            logger.warning("DataSubscription: xtquant 未安装，跳过全市场订阅")
            return
        try:
            self._whole_market_subscribe_id = xtdata.subscribe_whole_quote(["SH", "SZ"], callback=self._on_data)
            logger.info("DataSubscription: 全市场订阅已启动 [%s]", period)
        except Exception as e:
            logger.error("DataSubscription: 全市场订阅失败: %s", e, exc_info=True)

    def get_subscription_list(self) -> List[str]:
        with self._lock:
            return list(self._subscriptions.keys())

    def set_data_callback(self, callback: Callable[[Dict[str, TickData]], None]) -> None:
        """设置数据分发回调 — 由 StrategyRunner 注册"""
        self._data_callback = callback

    def resubscribe_all(self) -> None:
        """重连后重建全量订阅"""
        with self._lock:
            subscriptions = dict(self._subscriptions)
            whole_market = self._whole_market

        if whole_market:
            self.subscribe_whole_market()

        if subscriptions:
            period_groups: Dict[str, List[str]] = {}
            for code, period in subscriptions.items():
                period_groups.setdefault(period, []).append(code)
            for period, codes in period_groups.items():
                self.subscribe_stocks(codes, period)
        else:
            logger.info("DataSubscription: 没有已订阅的股票")

        logger.info("DataSubscription: 已完成重连后的订阅恢复（%d 只股票, whole=%s)",
                    len(subscriptions), whole_market)

    def start(self) -> None:
        """启动订阅（阻塞；在独立线程中调用）"""
        self._running = True
        logger.info("DataSubscription: 启动 xtdata.run()")
        if _XT_AVAILABLE:
            try:
                xtdata.run()
            except Exception as e:
                logger.error("DataSubscription: xtdata.run() 异常: %s", e, exc_info=True)
        else:
            # Mock 模式：空转
            while self._running:
                time.sleep(1)

    def stop(self) -> None:
        self._running = False
        logger.info("DataSubscription: 已停止")

    # ------------------------------------------------------------------ Internal callback

    def _on_data(self, raw_data: dict) -> None:
        """xtquant 推送回调 → 预处理 → 转发"""
        try:
            recv_time = datetime.now()
            self._last_recv_time = recv_time
            ticks: Dict[str, TickData] = {}

            for xt_code, data in raw_data.items():
                code = xt_code.split(".")[0] if "." in xt_code else xt_code
                tick = self._parse_tick(code, data, recv_time)
                ticks[code] = tick

                # 延迟告警
                if tick.latency_ms > self._latency_threshold * 1000:
                    logger.warning(
                        "DataSubscription: 数据延迟 %.1fs > %.1fs [%s]",
                        tick.latency_ms / 1000, self._latency_threshold, code
                    )

            if ticks and self._data_callback:
                self._data_callback(ticks)

        except Exception as e:
            logger.error("DataSubscription: _on_data 异常: %s", e, exc_info=True)

    # ------------------------------------------------------------------ Mock push (for testing)

    def push_mock_tick(self, code: str, price: float, volume: int = 1000) -> None:
        """测试用：手动推送一条模拟 tick"""
        recv_time = datetime.now()
        tick = TickData(
            stock_code=code,
            last_price=price,
            open=price,
            high=price,
            low=price,
            pre_close=price * 0.99,
            volume=volume,
            amount=price * volume,
            bid_prices=[price - 0.01, price - 0.02, price - 0.03,
                        price - 0.04, price - 0.05],
            bid_volumes=[100] * 5,
            ask_prices=[price + 0.01, price + 0.02, price + 0.03,
                        price + 0.04, price + 0.05],
            ask_volumes=[100] * 5,
            data_time=recv_time,
            recv_time=recv_time,
            latency_ms=0.0,
        )
        if self._data_callback:
            self._data_callback({code: tick})

    # ------------------------------------------------------------------ Private

    @staticmethod
    def _normalize_period(period: SubscriptionPeriod | str) -> str:
        if isinstance(period, SubscriptionPeriod):
            return period.value
        try:
            return SubscriptionPeriod(str(period)).value
        except ValueError:
            logger.warning("DataSubscription: 非法订阅周期 %s，回退为 tick", period)
            return SubscriptionPeriod.TICK.value

    @staticmethod
    def _parse_tick(code: str, data: dict, recv_time: datetime) -> TickData:
        """解析 xtquant 原始数据为 TickData"""
        # time 字段可能是时间戳（ms）或 datetime
        raw_time = data.get("time") or data.get("sysTime")
        data_time = recv_time
        latency_ms = 0.0
        if raw_time:
            try:
                if isinstance(raw_time, (int, float)):
                    data_time = datetime.fromtimestamp(raw_time / 1000)
                elif isinstance(raw_time, datetime):
                    data_time = raw_time
                latency_ms = (recv_time - data_time).total_seconds() * 1000
            except Exception:
                pass

        def _get(key, default=0.0):
            v = data.get(key)
            return v if v is not None else default

        bids_p = list(data.get("bidPrice", []))[:5]
        bids_v = list(data.get("bidVol", []))[:5]
        asks_p = list(data.get("askPrice", []))[:5]
        asks_v = list(data.get("askVol", []))[:5]

        return TickData(
            stock_code=code,
            last_price=float(_get("lastPrice")),
            open=float(_get("open")),
            high=float(_get("high")),
            low=float(_get("low")),
            pre_close=float(_get("lastClose")),
            volume=int(_get("volume")),
            amount=float(_get("amount")),
            bid_prices=bids_p,
            bid_volumes=bids_v,
            ask_prices=asks_p,
            ask_volumes=asks_v,
            data_time=data_time,
            recv_time=recv_time,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _to_xt(code: str) -> str:
        """6位代码 → xtquant 格式"""
        code = str(code).strip().zfill(6)
        if code.startswith(("6", "5")):
            return f"{code}.SH"
        return f"{code}.SZ"


__all__ = ["DataSubscriptionManager"]
