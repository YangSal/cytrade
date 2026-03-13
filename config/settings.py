"""
全局配置
所有可调参数集中在 Settings 类中，便于集中管理和修改
"""
import json
import os

from config.enums import SubscriptionPeriod


_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))


def _env_str(name: str, default: str = "") -> str:
    """读取字符串环境变量。

    这是最基础的读取函数，其余类型函数都遵循相同的容错思路：
    如果环境变量不存在，就返回默认值。
    """
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量，非法值时回退到默认值。"""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """读取浮点环境变量，非法值时回退到默认值。"""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量。

    常见真值写法包括：``1``、``true``、``yes``、``on``。
    其余情况统一视为 ``False``，但如果变量缺失则保留默认值。
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: list[str]) -> list[str]:
    """读取逗号分隔的列表环境变量。"""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_json_dict(name: str, default: dict) -> dict:
    """读取 JSON 字典环境变量。

    适合像远程数据库配置这种结构化参数。
    如果 JSON 解析失败，直接回退到默认配置，避免应用启动失败。
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else default
    except json.JSONDecodeError:
        return default


def _env_enum(name: str, enum_cls, default):
    """读取枚举环境变量，并确保值一定落在合法枚举范围内。"""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return enum_cls(raw)
    except ValueError:
        return default


def _coerce_subscription_period(value) -> SubscriptionPeriod:
    """把任意输入统一转换成 ``SubscriptionPeriod`` 枚举。

    这个函数主要用于：
    - 环境变量读取后的二次校验
    - 运行时通过构造参数覆盖配置时的类型归一化
    """
    if isinstance(value, SubscriptionPeriod):
        return value
    try:
        return SubscriptionPeriod(str(value))
    except ValueError:
        return SubscriptionPeriod.TICK


class Settings:
    """项目统一配置对象。

    这个类的职责是把“环境变量 + 默认值 + 运行时覆盖值”整合成一份
    可直接使用的配置快照。
    """
    # ---- QMT 连接 ----
    QMT_PATH: str = _env_str("QMT_PATH", "")
    ACCOUNT_ID: str = _env_str("ACCOUNT_ID", "")
    ACCOUNT_TYPE: str = _env_str("ACCOUNT_TYPE", "STOCK")
    ACCOUNT_PASSWORD: str = _env_str("ACCOUNT_PASSWORD", "")

    # ---- 数据订阅 ----
    SUBSCRIPTION_PERIOD: SubscriptionPeriod = _env_enum(
        "SUBSCRIPTION_PERIOD", SubscriptionPeriod, SubscriptionPeriod.TICK
    )
    DATA_LATENCY_THRESHOLD_SEC: float = _env_float("DATA_LATENCY_THRESHOLD_SEC", 10.0)   # 数据延迟告警阈值（秒）
    STRATEGY_PROCESS_THRESHOLD_MS: float = _env_float("STRATEGY_PROCESS_THRESHOLD_MS", 200)  # 单次策略处理超时阈值（毫秒）

    # ---- 日志 ----
    LOG_DIR: str = _env_str("LOG_DIR", "./logs")
    LOG_MAX_DAYS: int = _env_int("LOG_MAX_DAYS", 30)                     # 日志最长保存天数
    LOG_LEVEL: str = _env_str("LOG_LEVEL", "INFO")                    # INFO / DEBUG
    LOG_SUMMARY_MODE: bool = _env_bool("LOG_SUMMARY_MODE", False)             # True=仅打印成交与下单摘要

    # ---- 持仓管理 ----
    COST_METHOD: str = _env_str("COST_METHOD", "moving_average")        # moving_average / fifo
    FEE_TABLE_PATH: str = _env_str("FEE_TABLE_PATH", os.path.join(_CONFIG_DIR, "fee_rates.csv"))
    DEFAULT_BUY_FEE_RATE: float = _env_float("DEFAULT_BUY_FEE_RATE", 0.0001)
    DEFAULT_SELL_FEE_RATE: float = _env_float("DEFAULT_SELL_FEE_RATE", 0.0001)
    DEFAULT_STAMP_TAX_RATE: float = _env_float("DEFAULT_STAMP_TAX_RATE", 0.0003)

    # ---- 数据持久化 ----
    SQLITE_DB_PATH: str = _env_str("SQLITE_DB_PATH", "./data/db/cytrade.db")
    STATE_SAVE_DIR: str = _env_str("STATE_SAVE_DIR", "./saved_states")
    ENABLE_REMOTE_DB: bool = _env_bool("ENABLE_REMOTE_DB", False)             # 是否同步远程数据库
    REMOTE_DB_CONFIG: dict = _env_json_dict("REMOTE_DB_CONFIG", {
        "host": "",
        "port": 5432,
        "dbname": "",
        "user": "",
        "password": "",
    })

    # ---- 看门狗 ----
    WATCHDOG_INTERVAL_SEC: int = _env_int("WATCHDOG_INTERVAL_SEC", 30)            # 检查间隔（秒）
    DINGTALK_WEBHOOK_URL: str = _env_str("DINGTALK_WEBHOOK_URL", "")             # 钉钉 Webhook URL（待配置）
    DINGTALK_SECRET: str = _env_str("DINGTALK_SECRET", "")                  # 钉钉签名密钥
    # 定时推送持仓时间点
    POSITION_REPORT_TIMES: list = _env_list("POSITION_REPORT_TIMES", ["09:35", "11:35", "15:05"])
    CPU_ALERT_THRESHOLD: float = _env_float("CPU_ALERT_THRESHOLD", 80.0)          # CPU告警阈值（%）
    MEM_ALERT_THRESHOLD: float = _env_float("MEM_ALERT_THRESHOLD", 80.0)          # 内存告警阈值（%）

    # ---- Web 服务 ----
    WEB_HOST: str = _env_str("WEB_HOST", "0.0.0.0")
    WEB_PORT: int = _env_int("WEB_PORT", 8080)

    # ---- 日内会话控制 ----
    SESSION_START_TIME: str = _env_str("SESSION_START_TIME", "09:25")
    SESSION_EXIT_TIME: str = _env_str("SESSION_EXIT_TIME", "15:05")
    SESSION_POLL_INTERVAL_SEC: int = _env_int("SESSION_POLL_INTERVAL_SEC", 15)
    LOAD_PREVIOUS_STATE_ON_START: bool = _env_bool("LOAD_PREVIOUS_STATE_ON_START", True)

    # ---- 交易时间 ----
    MORNING_OPEN: str = _env_str("MORNING_OPEN", "09:30")
    MORNING_CLOSE: str = _env_str("MORNING_CLOSE", "11:30")
    AFTERNOON_OPEN: str = _env_str("AFTERNOON_OPEN", "13:00")
    AFTERNOON_CLOSE: str = _env_str("AFTERNOON_CLOSE", "15:00")

    # ---- 连接重连 ----
    RECONNECT_MAX_INTERVAL_SEC: int = _env_int("RECONNECT_MAX_INTERVAL_SEC", 60)       # 最大重连间隔
    RECONNECT_BASE_SEC: int = _env_int("RECONNECT_BASE_SEC", 1)                # 基础重连间隔（指数退避）
    RECONNECT_MAX_RETRIES: int = _env_int("RECONNECT_MAX_RETRIES", 0)             # 最大重连次数（0 表示无限重试）


    def __init__(self, **overrides):
        """支持在实例化时用关键字参数覆盖默认配置。

        这样测试代码或主程序装配时，可以很方便地构造一份临时配置，
        而不必真的修改环境变量。
        """
        for k, v in overrides.items():
            if hasattr(self, k):
                if k == "SUBSCRIPTION_PERIOD":
                    # 枚举字段需要做一次归一化，避免传入字符串后类型不一致。
                    v = _coerce_subscription_period(v)
                setattr(self, k, v)
            else:
                raise ValueError(f"Unknown config key: {k}")

    def ensure_dirs(self):
        """确保运行期需要的目录都已存在。

        主要包括：
        - 日志目录
        - 状态快照目录
        - SQLite 数据库所在目录
        """
        os.makedirs(self.LOG_DIR, exist_ok=True)
        os.makedirs(self.STATE_SAVE_DIR, exist_ok=True)
        db_dir = os.path.dirname(self.SQLITE_DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)


# 全局单例：多数模块直接 ``from config.settings import settings`` 即可使用。
settings = Settings()
