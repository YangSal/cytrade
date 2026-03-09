"""
看门狗模块
- 监控策略进程存活状态
- 监控数据订阅超时
- 监控交易连接状态
- 系统资源（CPU / 内存）
- 发送钉钉告警（告警分级）
- 定时推送持仓报告
"""
import asyncio
import hashlib
import hmac
import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Dict, Optional

from config.enums import AlertLevel
from monitor.logger import get_logger

logger = get_logger("system")

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False


class Watchdog:
    """系统监控看门狗。

    它周期性检查系统是否处于“健康状态”，并在发现异常时告警。
    """

    def __init__(self,
                 interval_sec: int = 30,
                 dingtalk_webhook: str = "",
                 dingtalk_secret: str = "",
                 cpu_threshold: float = 80.0,
                 mem_threshold: float = 80.0,
                 position_report_times=None,
                 position_manager=None,
                 connection_manager=None,
                 data_subscription=None):
        self._interval = interval_sec
        self._webhook = dingtalk_webhook
        self._secret = dingtalk_secret
        self._cpu_threshold = cpu_threshold
        self._mem_threshold = mem_threshold
        self._report_times = set(position_report_times or ["09:35", "11:35", "15:05"])
        self._position_mgr = position_manager
        self._conn_mgr = connection_manager
        self._data_sub = data_subscription

        # 心跳表 {source: last_heartbeat_time}
        self._heartbeats: Dict[str, float] = {}
        self._heartbeat_timeout = 120   # 2分钟无心跳告警

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._reported_times = set()    # 今日已推送过的时间点

    # ------------------------------------------------------------------ 控制

    def start(self) -> None:
        """启动看门狗后台线程。"""
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="watchdog"
        )
        self._thread.start()
        logger.info("Watchdog: 已启动（检查间隔 %ds）", self._interval)

    def stop(self) -> None:
        """停止看门狗。"""
        self._running = False
        logger.info("Watchdog: 已停止")

    # ------------------------------------------------------------------ 心跳

    def register_heartbeat(self, source: str) -> None:
        """其他模块调用此方法表示自己存活"""
        self._heartbeats[source] = time.time()

    # ------------------------------------------------------------------ 检查方法

    def check_strategy_alive(self) -> bool:
        """检查策略心跳是否超时"""
        now = time.time()
        for source, ts in list(self._heartbeats.items()):
            if now - ts > self._heartbeat_timeout:
                self.send_dingtalk_alert(
                    AlertLevel.ERROR,
                    f"[看门狗] {source} 心跳超时 {(now-ts)/60:.1f} 分钟，可能卡死！"
                )
                return False
        return True

    def check_data_subscription(self) -> bool:
        """检查数据订阅是否超时"""
        if not self._data_sub:
            return True
        last = getattr(self._data_sub, "_last_recv_time", None)
        if last is None:
            return True
        elapsed = (datetime.now() - last).total_seconds()
        if elapsed > 60 and self._is_trading_time():
            self.send_dingtalk_alert(
                AlertLevel.WARNING,
                f"[看门狗] 数据订阅超时 {elapsed:.0f}s（上次推送 {last.strftime('%H:%M:%S')}）"
            )
            return False
        return True

    def check_connection(self) -> bool:
        """检查交易连接状态"""
        if not self._conn_mgr:
            return True
        if not self._conn_mgr.is_connected():
            self.send_dingtalk_alert(
                AlertLevel.ERROR,
                "[看门狗] 交易连接断开！正在重连..."
            )
            return False
        return True

    def check_system_resources(self) -> dict:
        """检查 CPU 和内存占用情况。"""
        result = {"cpu": 0.0, "memory": 0.0}
        if not _PSUTIL:
            return result
        try:
            result["cpu"] = psutil.cpu_percent(interval=1)
            result["memory"] = psutil.virtual_memory().percent
            if result["cpu"] > self._cpu_threshold:
                self.send_dingtalk_alert(
                    AlertLevel.WARNING,
                    f"[看门狗] CPU 使用率 {result['cpu']:.1f}% 超过阈值 {self._cpu_threshold}%"
                )
            if result["memory"] > self._mem_threshold:
                self.send_dingtalk_alert(
                    AlertLevel.WARNING,
                    f"[看门狗] 内存使用率 {result['memory']:.1f}% 超过阈值 {self._mem_threshold}%"
                )
        except Exception as e:
            logger.error("Watchdog: 系统资源检查失败: %s", e, exc_info=True)
        return result

    def send_position_report(self) -> None:
        """发送持仓汇总到钉钉"""
        if not self._position_mgr:
            return
        try:
            summary = self._position_mgr.get_position_summary()
            msg = (
                f"📊 持仓报告 {datetime.now().strftime('%H:%M')}\n"
                f"持仓数量: {summary['positions_count']}\n"
                f"总市值: ¥{summary['total_market_value']:,.2f}\n"
                f"浮动盈亏: ¥{summary['total_unrealized_pnl']:,.2f}\n"
                f"已实现盈亏: ¥{summary['total_realized_pnl']:,.2f}\n"
                f"累计手续费: ¥{summary['total_commission']:,.2f}"
            )
            self.send_dingtalk_alert(AlertLevel.INFO, msg)
        except Exception as e:
            logger.error("Watchdog: 持仓报告发送失败: %s", e, exc_info=True)

    def send_dingtalk_alert(self, level: AlertLevel, message: str) -> None:
        """发送钉钉告警消息"""
        if not self._webhook:
            logger.log(
                40 if level == AlertLevel.ERROR else
                30 if level == AlertLevel.WARNING else 20,
                "[Watchdog->钉钉] %s", message
            )
            return
        try:
            prefix = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🚨"}.get(level.value, "")
            text = f"{prefix} [{level.value}] {message}"
            url = self._signed_url()
            payload = json.dumps({
                "msgtype": "text",
                "text": {"content": text}
            }, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
            logger.info("Watchdog: 钉钉告警发送成功 [%s]", level.value)
        except Exception as e:
            logger.error("Watchdog: 钉钉告警发送失败: %s", e, exc_info=True)

    # ------------------------------------------------------------------ Private

    def _run_loop(self) -> None:
        """看门狗主循环。"""
        while self._running:
            try:
                self.check_strategy_alive()
                self.check_data_subscription()
                self.check_connection()
                self.check_system_resources()
                self._check_report_times()
            except Exception as e:
                logger.error("Watchdog: 主循环异常: %s", e, exc_info=True)
            time.sleep(self._interval)

    def _check_report_times(self) -> None:
        """检查是否到了定时推送时间"""
        now_str = datetime.now().strftime("%H:%M")
        key = f"{datetime.now().date()}-{now_str}"
        if now_str in self._report_times and key not in self._reported_times:
            self._reported_times.add(key)
            self.send_position_report()

    def _signed_url(self) -> str:
        """生成带签名的钉钉 Webhook URL"""
        if not self._secret:
            return self._webhook
        ts = str(int(time.time() * 1000))
        sign_str = f"{ts}\n{self._secret}"
        sig = hmac.new(
            self._secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()
        import base64
        enc = urllib.parse.quote_plus(base64.b64encode(sig).decode("utf-8"))
        return f"{self._webhook}&timestamp={ts}&sign={enc}"

    @staticmethod
    def _is_trading_time() -> bool:
        """粗略判断当前是否处于交易时段。"""
        t = datetime.now().strftime("%H:%M")
        return ("09:25" <= t <= "11:35") or ("12:55" <= t <= "15:05")


__all__ = ["Watchdog"]
