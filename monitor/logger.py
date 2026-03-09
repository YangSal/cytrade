"""
日志模块
- 结构化 JSON 日志
- 滚动存储 + 超时自动清理
- 按日期压缩归档
- 分级：交易日志、系统日志、调试日志
- 动态切换日志级别
- 仅摘要模式（避免终端刷屏）
"""
import gzip
import logging
import logging.handlers
import os
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

try:
    from loguru import logger as _loguru_logger
    _USE_LOGURU = True
except ImportError:
    _USE_LOGURU = False

try:
    from pythonjsonlogger import jsonlogger
    _USE_JSON = True
except ImportError:
    _USE_JSON = False


class _SummaryFilter(logging.Filter):
    """摘要模式过滤器：只放行含 [ORDER] / [TRADE] 标签的日志"""
    def filter(self, record: logging.LogRecord) -> bool:
        """只允许订单与成交摘要日志通过。"""
        msg = record.getMessage()
        return "[ORDER]" in msg or "[TRADE]" in msg


class LogManager:
    """日志管理器（单例）。

    整个项目只需要一套日志配置，因此这里使用单例模式，
    防止多个模块重复创建 handler，导致日志重复输出。
    """

    _instance: Optional["LogManager"] = None
    _lock = threading.Lock()

    # 日志名称常量
    TRADE_LOG = "trade"      # 订单、成交
    SYSTEM_LOG = "system"    # 连接、错误
    DEBUG_LOG = "debug"      # 调试

    def __new__(cls, *args, **kwargs):
        """确保全局只创建一个 ``LogManager`` 实例。"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, log_dir: str = "./logs", max_days: int = 30,
                 level: str = "INFO", summary_mode: bool = False):
        """初始化日志系统配置。"""
        if self._initialized:
            return
        self._initialized = True
        self._log_dir = log_dir
        self._max_days = max_days
        self._level = level.upper()
        self._summary_mode = summary_mode
        self._loggers: dict[str, logging.Logger] = {}
        self._summary_filter = _SummaryFilter()
        os.makedirs(log_dir, exist_ok=True)
        self.setup_logging()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_logger(self, name: str = "system") -> logging.Logger:
        """获取指定名称的 logger，不存在时自动创建"""
        if name not in self._loggers:
            self._loggers[name] = self._create_logger(name)
        return self._loggers[name]

    def setup_logging(self) -> None:
        """初始化默认日志分类。"""
        for name in [self.TRADE_LOG, self.SYSTEM_LOG, self.DEBUG_LOG]:
            self._loggers[name] = self._create_logger(name)
        # 默认 logger 代理 system
        self._loggers["default"] = self._loggers[self.SYSTEM_LOG]

    def set_log_level(self, level: str) -> None:
        """动态切换日志级别"""
        self._level = level.upper()
        numeric = getattr(logging, self._level, logging.INFO)
        for lgr in self._loggers.values():
            lgr.setLevel(numeric)

    def set_summary_mode(self, enabled: bool) -> None:
        """开关：仅打印成交与下单摘要（抑制终端刷屏）"""
        self._summary_mode = enabled
        for lgr in self._loggers.values():
            for hdlr in lgr.handlers:
                if isinstance(hdlr, logging.StreamHandler):
                    if enabled:
                        hdlr.addFilter(self._summary_filter)
                    else:
                        hdlr.removeFilter(self._summary_filter)

    def cleanup_old_logs(self) -> None:
        """清理超过保存期限的日志，并压缩旧日志。"""
        cutoff = datetime.now() - timedelta(days=self._max_days)
        for fname in os.listdir(self._log_dir):
            fpath = os.path.join(self._log_dir, fname)
            if not os.path.isfile(fpath):
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
            # 压缩较旧（但未超期）的 .log 文件
            if fname.endswith(".log") and mtime < datetime.now() - timedelta(days=1):
                self._compress_log(fpath)
            # 删除超期文件（含 .gz）
            if mtime < cutoff:
                os.remove(fpath)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _create_logger(self, name: str) -> logging.Logger:
        """为指定分类创建 logger 与 handler。"""
        lgr = logging.getLogger(f"cytrade.{name}")
        lgr.setLevel(getattr(logging, self._level, logging.INFO))
        lgr.propagate = False

        if lgr.handlers:
            return lgr

        # ---- 滚动文件 Handler ----
        log_file = os.path.join(self._log_dir, f"{name}.log")
        fh = logging.handlers.TimedRotatingFileHandler(
            log_file, when="midnight", backupCount=self._max_days,
            encoding="utf-8"
        )
        fh.suffix = "%Y%m%d"

        if _USE_JSON:
            formatter = jsonlogger.JsonFormatter(
                fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S"
            )
        else:
            formatter = logging.Formatter(
                fmt='{"time":"%(asctime)s","logger":"%(name)s","level":"%(levelname)s","msg":"%(message)s"}',
                datefmt="%Y-%m-%dT%H:%M:%S"
            )
        fh.setFormatter(formatter)
        lgr.addHandler(fh)

        # ---- 控制台 Handler ----
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(name)s | %(message)s",
            datefmt="%H:%M:%S"
        ))
        if self._summary_mode:
            ch.addFilter(self._summary_filter)
        lgr.addHandler(ch)

        return lgr

    @staticmethod
    def _compress_log(fpath: str) -> None:
        """把旧日志压缩成 ``.gz`` 文件，减少磁盘占用。"""
        gz_path = fpath + ".gz"
        if os.path.exists(gz_path):
            return
        try:
            with open(fpath, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(fpath)
        except Exception:
            pass


# -------------------------------------------------------------------
# 模块级快捷函数
# -------------------------------------------------------------------

def get_logger(name: str = "system") -> logging.Logger:
    """快捷获取 logger，无需持有 LogManager 实例"""
    return LogManager().get_logger(name)


__all__ = ["LogManager", "get_logger"]
