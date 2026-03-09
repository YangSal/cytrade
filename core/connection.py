"""
交易连接管理模块
- 管理与 QMT 客户端的 XtQuant 连接
- 指数退避自动重连
- 心跳保持
- 提供 trader 实例供交易模块使用
"""
import threading
import time
from typing import Optional

from monitor.logger import get_logger

logger = get_logger("system")

try:
    from xtquant.xttrader import XtQuantTrader
    from xtquant.xttype import StockAccount
    _XT_AVAILABLE = True
except ImportError:
    _XT_AVAILABLE = False
    # Mock objects for development without QMT installed
    class XtQuantTrader:  # type: ignore
        def __init__(self, path, session_id):
            self._path = path
            self._session_id = session_id
            self._connected = False
            self._subscribed = False

        def connect(self):
            logger.warning("XtQuantTrader [MOCK] connect() called")
            self._connected = True
            return 0

        def start(self):
            pass

        def stop(self):
            self._connected = False

        def register_callback(self, callback):
            pass

        def subscribe_callback(self, callback):
            pass

        def subscribe(self, account):
            self._subscribed = True
            return 0

        def is_connected(self):
            return self._connected

    class StockAccount:  # type: ignore
        def __init__(self, account_id, account_type="STOCK"):
            self.account_id = account_id
            self.account_type = account_type


class ConnectionManager:
    """XtQuant 交易连接管理器（单例）"""

    def __init__(self, qmt_path: str, account_id: str,
                 base_interval: int = 1, max_interval: int = 60,
                 max_retries: Optional[int] = None):
        self._qmt_path = qmt_path
        self._account_id = account_id
        self._base_interval = base_interval
        self._max_interval = max_interval
        self._max_retries = max_retries

        self._trader: Optional[XtQuantTrader] = None
        self._account: Optional[StockAccount] = None
        self._callback = None
        self._connected = False
        self._lock = threading.Lock()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._reconnect_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()
        self._reconnect_callbacks = []

        # 会话 ID：每次启动使用时间戳，避免冲突
        self._session_id = int(time.time()) % 100000

    # ------------------------------------------------------------------ Public

    def connect(self) -> bool:
        """连接到 QMT 客户端，返回是否成功"""
        with self._lock:
            try:
                self._stop_heartbeat.set()
                if self._trader:
                    try:
                        self._trader.stop()
                    except Exception:
                        pass
                self._trader = XtQuantTrader(self._qmt_path, self._session_id)
                self._account = StockAccount(self._account_id)
                if self._callback:
                    self._trader.register_callback(self._callback)
                self._trader.start()
                ret = self._trader.connect()
                if ret == 0:
                    subscribe_ret = self._trader.subscribe(self._account)
                    if subscribe_ret != 0:
                        logger.error("ConnectionManager: 账户订阅失败，返回码: %d", subscribe_ret)
                        self._connected = False
                        return False
                    self._connected = True
                    logger.info("ConnectionManager: 连接 QMT 成功并完成账户订阅 (session=%d)", self._session_id)
                    self._start_heartbeat()
                    return True
                else:
                    logger.error("ConnectionManager: 连接 QMT 失败，返回码: %d", ret)
                    self._connected = False
                    return False
            except Exception as e:
                logger.error("ConnectionManager: 连接异常: %s", e, exc_info=True)
                self._connected = False
                return False

    def disconnect(self) -> None:
        """断开连接"""
        self._stop_heartbeat.set()
        if self._trader:
            try:
                self._trader.stop()
            except Exception as e:
                logger.warning("ConnectionManager: 断开时异常: %s", e)
        self._connected = False
        logger.info("ConnectionManager: 已断开连接")

    def reconnect(self) -> bool:
        """指数退避重连"""
        interval = self._base_interval
        retries = 0
        while True:
            if self._max_retries is not None and retries >= self._max_retries:
                logger.error("ConnectionManager: 重连超过最大次数 %d，停止重连", self._max_retries)
                return False
            logger.warning("ConnectionManager: 尝试重连（等待 %ds）...", interval)
            time.sleep(interval)
            retries += 1
            if self.connect():
                logger.info("ConnectionManager: 重连成功")
                for callback in self._reconnect_callbacks:
                    try:
                        callback()
                    except Exception as e:
                        logger.error("ConnectionManager: 重连回调异常: %s", e, exc_info=True)
                return True
            interval = min(interval * 2, self._max_interval)

    def get_trader(self) -> Optional[XtQuantTrader]:
        """获取交易通道对象"""
        return self._trader

    def is_connected(self) -> bool:
        if self._trader and hasattr(self._trader, "is_connected"):
            try:
                return bool(self._trader.is_connected())
            except Exception:
                pass
        return self._connected

    def register_callback(self, callback) -> None:
        """注册交易回调（需在 connect 前调用）"""
        self._callback = callback
        if self._trader:
            self._trader.register_callback(callback)

    def register_reconnect_callback(self, callback) -> None:
        """注册重连成功回调（用于恢复订阅等）"""
        self._reconnect_callbacks.append(callback)

    @property
    def account(self) -> Optional[StockAccount]:
        return self._account

    def on_disconnected(self) -> None:
        """由 callback 调用，触发重连"""
        logger.warning("ConnectionManager: 检测到连接断开，启动重连...")
        self._connected = False
        with self._lock:
            if self._reconnect_thread and self._reconnect_thread.is_alive():
                logger.warning("ConnectionManager: 重连线程已在运行，跳过重复启动")
                return
            self._reconnect_thread = threading.Thread(
                target=self.reconnect, daemon=True, name="reconnect"
            )
            self._reconnect_thread.start()

    # ------------------------------------------------------------------ Private

    def _start_heartbeat(self) -> None:
        self._stop_heartbeat.clear()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="heartbeat"
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop_heartbeat.wait(timeout=30):
            try:
                if not self._connected:
                    break
                # xtquant 目前无独立 ping 接口；依赖 on_disconnected 回调感知断线
                logger.debug("ConnectionManager: 心跳 OK")
            except Exception as e:
                logger.warning("ConnectionManager: 心跳异常: %s", e)


__all__ = ["ConnectionManager"]
