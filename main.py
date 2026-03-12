"""cytrade 主程序入口。

本文件不负责承载具体交易策略逻辑，而是负责把系统按“可运行”的方式组装起来。
当前主程序采用“两层运行模型”：

1. 父进程：
    - 常驻运行。
    - 使用 `BlockingScheduler` 维护日级计划任务。
    - 到达设定时刻后，通过 `ProcessPoolExecutor` 启动独立子进程。

2. 子进程：
    - 承担一次完整的交易日会话。
    - 在该进程内完成 QMT 连接、行情订阅、策略恢复与运行、Web 服务、看门狗等全部工作。
    - 收盘后保存状态并主动退出，从而释放进程资源。

之所以使用这种结构，是为了把“长期常驻调度”与“日内交易会话”解耦：

- 父进程只做轻量调度，不持有交易连接和行情资源。
- 子进程只负责单次交易日会话，结束后整体退出，避免残留线程、连接或内存状态跨天累积。
- 若子进程因异常退出，不会直接污染父进程调度器，可在下一次计划任务时重新拉起。

阅读本文件时，建议按如下顺序理解：

1. `build_app()`：理解子进程内部有哪些模块、如何装配。
2. `_run_managed_session()`：理解单次交易日会话在子进程中如何完整运行。
3. `run_daily_session()`：理解一次会话的日历与时间控制。
4. `_launch_session_in_process()`：理解父进程如何为单次会话创建独立子进程。
5. `run_scheduler_service()`：理解父进程如何长期调度整个系统。
"""
import sys
import importlib
import os
import signal
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import Iterable

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import Settings
from config.fee_schedule import FeeSchedule
from monitor.logger import LogManager, get_logger
from data.manager import DataManager
from core.connection import ConnectionManager
from core.callback import MyXtQuantTraderCallback
from core.data_subscription import DataSubscriptionManager
from core.trading_calendar import is_market_day
from trading.order_manager import OrderManager
from trading.executor import TradeExecutor
from position.manager import PositionManager
from strategy.runner import StrategyRunner
from monitor.watchdog import Watchdog


def _to_strategy_spec(strategy_class_or_spec) -> str:
    """把策略类或文本描述统一转换为可跨进程传输的导入路径。"""
    if isinstance(strategy_class_or_spec, str):
        return strategy_class_or_spec

    module_name = getattr(strategy_class_or_spec, "__module__", "")
    qualname = getattr(strategy_class_or_spec, "__name__", "")
    if not module_name or not qualname:
        raise ValueError(f"无法序列化策略定义: {strategy_class_or_spec!r}")
    return f"{module_name}:{qualname}"


def _normalize_strategy_specs(strategy_classes=None) -> list[str]:
    """把策略定义列表标准化成 ``module:Class`` 形式。"""
    return [_to_strategy_spec(item) for item in (strategy_classes or [])]


def _resolve_strategy_specs(strategy_specs: Iterable[str]) -> list[type]:
    """把 ``module:Class`` 文本描述解析成真实策略类对象。"""
    resolved = []
    for spec in strategy_specs:
        module_name, class_name = str(spec).split(":", 1)
        module = importlib.import_module(module_name)
        resolved.append(getattr(module, class_name))
    return resolved


def build_app(strategy_classes=None, settings: Settings = None):
    """构建并连接所有核心模块。

    这是整个程序的“装配函数”，只负责依赖注入和对象连接，
    不负责真正进入运行循环。

    Args:
        strategy_classes: 需要托管的策略类列表。
        settings: 可选配置对象；不传时使用默认配置。

    Returns:
        一个包含所有核心模块实例的字典，便于测试和主程序复用。

    运行说明：
        这个函数可以理解为“交易子进程内部的容器装配阶段”。它会依次完成：

        1. 初始化日志系统，确保后续运行日志有稳定输出。
        2. 初始化数据管理器，准备 SQLite、状态快照目录和可选远程同步能力。
        3. 初始化费率配置，统一交易费用计算规则。
        4. 初始化连接管理器，为后续 QMT 连接和重连提供统一入口。
        5. 初始化订单管理器、持仓管理器、交易执行器。
        6. 建立回调链路：柜台回报 -> 订单 -> 持仓/策略。
        7. 初始化行情订阅管理器。
        8. 初始化策略运行器，并注入状态恢复、账户预检查和延迟监控能力。
        9. 初始化看门狗，并把心跳与告警回调挂到策略运行器上。

        注意：
        - 这里只完成“对象装配”，不会真正连接 QMT，也不会启动订阅和策略运行。
        - 真正的启动动作发生在 `_run_managed_session()` 中。
    """
    settings = settings or Settings()
    # 先准备运行目录，避免后续日志、数据库、状态文件写入失败。
    settings.ensure_dirs()

    # ---- 日志 ----
    log_mgr = LogManager(
        log_dir=settings.LOG_DIR,
        max_days=settings.LOG_MAX_DAYS,
        level=settings.LOG_LEVEL,
        summary_mode=settings.LOG_SUMMARY_MODE,
    )
    logger = get_logger("system")
    logger.info("=" * 50)
    logger.info("cytrade 启动")

    # ---- 数据管理 ----
    data_mgr = DataManager(
        db_path=settings.SQLITE_DB_PATH,
        state_dir=settings.STATE_SAVE_DIR,
        remote_cfg=settings.REMOTE_DB_CONFIG,
    )
    if settings.ENABLE_REMOTE_DB:
        data_mgr.set_remote_enabled(True)

    # ``fee_schedule`` 统一封装买卖佣金、印花税和 T+0 属性判断。
    fee_schedule = FeeSchedule(
        file_path=settings.FEE_TABLE_PATH,
        default_buy_fee_rate=settings.DEFAULT_BUY_FEE_RATE,
        default_sell_fee_rate=settings.DEFAULT_SELL_FEE_RATE,
        default_stamp_tax_rate=settings.DEFAULT_STAMP_TAX_RATE,
    )

    # ---- 交易连接 ----
    conn_mgr = ConnectionManager(
        qmt_path=settings.QMT_PATH,
        account_id=settings.ACCOUNT_ID,
        account_type=settings.ACCOUNT_TYPE,
        base_interval=settings.RECONNECT_BASE_SEC,
        max_interval=settings.RECONNECT_MAX_INTERVAL_SEC,
        max_retries=(settings.RECONNECT_MAX_RETRIES
                     if settings.RECONNECT_MAX_RETRIES > 0 else None),
    )

    # ---- 订单管理 ----
    order_mgr = OrderManager(data_manager=data_mgr, fee_schedule=fee_schedule)

    # ---- 持仓管理 ----
    pos_mgr = PositionManager(
        cost_method=settings.COST_METHOD,
        data_manager=data_mgr,
        fee_schedule=fee_schedule,
    )

    # ---- 注册回调链：成交 → 持仓 ----
    order_mgr.set_position_callback(pos_mgr.on_trade_callback)

    # ---- 交易执行器 ----
    trade_exec = TradeExecutor(conn_mgr, order_mgr, pos_mgr)

    # ---- XtQuant 回调 ----
    callback = MyXtQuantTraderCallback(
        order_manager=order_mgr,
        connection_manager=conn_mgr,
    )
    conn_mgr.register_callback(callback)

    # ---- 数据订阅 ----
    # 行情订阅模块与交易连接模块解耦，便于重连后独立恢复订阅。
    data_sub = DataSubscriptionManager(
        latency_threshold_sec=settings.DATA_LATENCY_THRESHOLD_SEC,
        default_period=settings.SUBSCRIPTION_PERIOD,
    )

    # ---- 策略运行 ----
    runner = StrategyRunner(
        data_subscription=data_sub,
        trade_executor=trade_exec,
        position_manager=pos_mgr,
        data_manager=data_mgr,
        connection_manager=conn_mgr,
        strategy_classes=strategy_classes or [],
        load_previous_state_on_start=settings.LOAD_PREVIOUS_STATE_ON_START,
        latency_threshold_sec=settings.DATA_LATENCY_THRESHOLD_SEC,
        process_threshold_ms=settings.STRATEGY_PROCESS_THRESHOLD_MS,
    )

    # 注册“订单状态变化 -> 策略对象”的回调。
    # 这样策略才能在成交、撤单、废单后及时更新自己的内部状态。
    order_mgr.set_strategy_callback(runner.dispatch_order_update)

    # 网络断开后，连接模块会负责重连；
    # 这里再把“重连成功后的补偿动作”挂进去，自动恢复行情订阅。
    conn_mgr.register_reconnect_callback(data_sub.resubscribe_all)

    # ---- 看门狗 ----
    watchdog = Watchdog(
        interval_sec=settings.WATCHDOG_INTERVAL_SEC,
        dingtalk_webhook=settings.DINGTALK_WEBHOOK_URL,
        dingtalk_secret=settings.DINGTALK_SECRET,
        cpu_threshold=settings.CPU_ALERT_THRESHOLD,
        mem_threshold=settings.MEM_ALERT_THRESHOLD,
        position_report_times=settings.POSITION_REPORT_TIMES,
        position_manager=pos_mgr,
        connection_manager=conn_mgr,
        data_subscription=data_sub,
    )

    # 行情到达时刷新看门狗心跳
    runner.set_heartbeat_callback(watchdog.register_heartbeat)
    runner.set_alert_callback(watchdog.send_dingtalk_alert)

    # 返回装配好的上下文，方便：
    # 1. `run()` 直接复用。
    # 2. 测试代码精确断言模块装配关系。
    return {
        "settings": settings,
        "log_mgr": log_mgr,
        "data_mgr": data_mgr,
        "fee_schedule": fee_schedule,
        "conn_mgr": conn_mgr,
        "order_mgr": order_mgr,
        "pos_mgr": pos_mgr,
        "trade_exec": trade_exec,
        "callback": callback,
        "data_sub": data_sub,
        "runner": runner,
        "watchdog": watchdog,
    }


def run(strategy_classes=None, settings: Settings = None):
    """启动主程序。

    这个函数负责真正运行系统，包括：
    - 建立 QMT 连接
    - 启动 Web 服务
    - 启动看门狗
    - 启动策略运行器
    - 启动行情订阅线程
    - 监听退出信号

    运行说明：
        这是项目早期的“直接运行模式”。调用它后，当前进程会直接承担所有交易职责。
        也就是说：

        - 当前进程自己连接 QMT。
        - 当前进程自己启动行情订阅。
        - 当前进程自己运行策略和 Web 服务。

        这种模式适合：
        - 本地快速调试。
        - 单次手工启动验证。

        但对于长期稳定运行，当前项目更推荐 `run_scheduler_service()`：
        让父进程只负责调度，把完整交易会话放到独立子进程中执行。
    """
    ctx = build_app(strategy_classes, settings)
    logger = get_logger("system")
    # 这里把常用模块从上下文字典中取出，
    # 让后续启动逻辑更直观，也避免频繁写 `ctx[...]`。
    settings = ctx["settings"]
    conn_mgr = ctx["conn_mgr"]
    runner = ctx["runner"]
    watchdog = ctx["watchdog"]
    data_sub = ctx["data_sub"]

    # ---- 优雅退出 ----
    _stop_event = threading.Event()

    def _signal_handler(sig, frame):
        """统一处理 Ctrl+C / 终止信号，尽量优雅退出。"""
        logger.info("收到退出信号 (%s)，正在关闭...", sig)
        runner.stop()
        watchdog.stop()
        data_sub.stop()
        conn_mgr.disconnect()
        _stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # ---- 连接 QMT ----
    if not conn_mgr.connect():
        logger.error("无法连接 QMT，退出")
        return

    # ---- Web 服务 ----
    # Web 层是可选能力，因此这里采用 `try` 包裹，
    # 避免缺少 FastAPI/uvicorn 时影响核心交易链路启动。
    try:
        from web.backend.main import init_app_context, run_server
        from web.backend import routes as web_routes
        # 把核心对象注入 Web 层，供 API 路由直接访问。
        init_app_context(
            strategy_runner=runner,
            position_manager=ctx["pos_mgr"],
            order_manager=ctx["order_mgr"],
            data_manager=ctx["data_mgr"],
            connection_manager=conn_mgr,
            trade_executor=ctx["trade_exec"],
        )
        run_server(host=settings.WEB_HOST, port=settings.WEB_PORT)
        if getattr(web_routes, "_ws_manager", None):
            # 成交发生后，主动推送给前端，减少轮询压力。
            ctx["order_mgr"].set_trade_callback(web_routes._ws_manager.notify_trade_update)
    except Exception as e:
        logger.warning("Web 服务未启动（可能缺少 fastapi/uvicorn）: %s", e)

    # ---- 看门狗 ----
    watchdog.start()

    # ---- 策略启动 ----
    runner.start()
    watchdog.register_heartbeat("strategy_runner")

    # ``xtdata.run()`` 是阻塞式调用，所以放到子线程里运行。
    # 主线程只负责等待退出信号，避免主程序被卡死在行情循环里。
    data_thread = threading.Thread(
        target=data_sub.start, daemon=True, name="data-sub"
    )
    data_thread.start()

    logger.info("cytrade 运行中。按 Ctrl+C 退出。")
    _stop_event.wait()
    logger.info("cytrade 已退出")


def _parse_hhmm(value: str) -> tuple[int, int]:
    """把 ``HH:MM`` 字符串解析为小时和分钟。"""
    text = str(value or "").strip()
    try:
        hour_str, minute_str = text.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
    except Exception as exc:
        raise ValueError(f"非法时间格式: {value!r}，必须是 HH:MM") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"非法时间范围: {value!r}")
    return hour, minute


def _build_session_time(anchor: datetime, hhmm: str) -> datetime:
    """基于指定日期构造会话时间点。"""
    hour, minute = _parse_hhmm(hhmm)
    return anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _wait_until_session_start(settings: Settings,
                              stop_event: threading.Event,
                              now_provider=None,
                              sleep_fn=None) -> bool:
    """等待直到日内会话启动时间。

    Returns:
        bool: 成功等到启动时刻返回 `True`；若等待过程中收到停止信号则返回 `False`。

    运行说明：
        这个函数主要用于“手工执行一次日会话”时的时间对齐。
        如果当前时刻还早于 `SESSION_START_TIME`，函数不会立刻启动交易系统，
        而是按固定轮询间隔休眠等待，直到满足启动条件。

        这样做可以避免：
        - 人工在过早时间启动程序后，系统立刻建立不必要的交易连接。
        - 盘前长时间占用行情/交易资源。
    """
    now_provider = now_provider or datetime.now
    sleep_fn = sleep_fn or time.sleep

    while not stop_event.is_set():
        now = now_provider()
        start_at = _build_session_time(now, settings.SESSION_START_TIME)
        if now >= start_at:
            return True

        remaining = max(0.0, (start_at - now).total_seconds())
        wait_seconds = min(max(int(settings.SESSION_POLL_INTERVAL_SEC), 1), remaining)
        logger = get_logger("system")
        logger.info("距离会话启动还有 %.0f 秒，继续等待", remaining)
        sleep_fn(wait_seconds)

    return False


def _run_managed_session(strategy_classes=None, settings: Settings = None,
                         now_provider=None, sleep_fn=None):
    """运行一个完整的交易日会话，并在收盘后自动退出。

    运行说明：
        这是整个主程序最核心的“子进程交易会话”入口。
        一旦进入这里，说明当前进程已经被确定为“本次交易日的执行进程”。

        该函数内部会按顺序完成以下步骤：

        1. 调用 `build_app()` 装配所有运行模块。
        2. 注册进程级退出信号处理器，保证 Ctrl+C / kill 时尽量优雅关闭。
        3. 建立 QMT 连接；若连接失败则直接结束本次会话。
        4. 尝试启动 Web 服务，并把核心对象注入 Web 路由层。
        5. 启动看门狗，使其监控心跳、连接状态和数据接收情况。
        6. 启动策略运行器：自动恢复状态、执行预检查、订阅策略相关标的。
        7. 启动行情订阅线程，让 `xtdata.run()` 在后台持续接收行情。
        8. 启动收盘守护线程，达到 `SESSION_EXIT_TIME` 后统一关闭系统。
        9. 主线程阻塞等待 `stop_event`，直到收到退出信号或收盘退出条件。

        关闭阶段会统一执行：
        - `runner.stop()`：保存状态并停止策略。
        - `watchdog.stop()`：停止后台监控。
        - `data_sub.stop()`：停止行情订阅循环。
        - `conn_mgr.disconnect()`：断开 QMT 连接。
        - `data_mgr.close()`：关闭数据管理器持有的外部资源。

        这样能够确保“一个交易日会话结束 = 一个独立进程彻底结束”，
        从而减少跨天资源残留问题。
    """
    ctx = build_app(strategy_classes, settings)
    logger = get_logger("system")
    settings = ctx["settings"]
    conn_mgr = ctx["conn_mgr"]
    runner = ctx["runner"]
    watchdog = ctx["watchdog"]
    data_sub = ctx["data_sub"]
    data_mgr = ctx["data_mgr"]
    now_provider = now_provider or datetime.now
    sleep_fn = sleep_fn or time.sleep

    stop_event = threading.Event()
    shutdown_lock = threading.Lock()
    data_thread = None

    def _shutdown(reason: str) -> None:
        """统一执行一次幂等关闭，确保资源尽量被释放。"""
        with shutdown_lock:
            if stop_event.is_set():
                return
            logger.info("cytrade 正在关闭：%s", reason)
            stop_event.set()

        try:
            runner.stop()
        except Exception as exc:
            logger.error("关闭策略运行器失败: %s", exc, exc_info=True)

        try:
            watchdog.stop()
        except Exception as exc:
            logger.error("关闭看门狗失败: %s", exc, exc_info=True)

        try:
            data_sub.stop()
        except Exception as exc:
            logger.error("停止行情订阅失败: %s", exc, exc_info=True)

        try:
            conn_mgr.disconnect()
        except Exception as exc:
            logger.error("断开交易连接失败: %s", exc, exc_info=True)

        try:
            data_mgr.close()
        except Exception as exc:
            logger.error("关闭数据管理器失败: %s", exc, exc_info=True)

    def _signal_handler(sig, frame):
        """统一处理 Ctrl+C / 终止信号。"""
        _shutdown(f"收到退出信号 {sig}")

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if not conn_mgr.connect():
        logger.error("无法连接 QMT，退出")
        _shutdown("交易连接建立失败")
        return

    try:
        from web.backend.main import init_app_context, run_server
        from web.backend import routes as web_routes
        init_app_context(
            strategy_runner=runner,
            position_manager=ctx["pos_mgr"],
            order_manager=ctx["order_mgr"],
            data_manager=data_mgr,
            connection_manager=conn_mgr,
            trade_executor=ctx["trade_exec"],
        )
        run_server(host=settings.WEB_HOST, port=settings.WEB_PORT)
        if getattr(web_routes, "_ws_manager", None):
            ctx["order_mgr"].set_trade_callback(web_routes._ws_manager.notify_trade_update)
    except Exception as e:
        logger.warning("Web 服务未启动（可能缺少 fastapi/uvicorn）: %s", e)

    watchdog.start()
    runner.start()
    watchdog.register_heartbeat("strategy_runner")

    data_thread = threading.Thread(target=data_sub.start, daemon=True, name="data-sub")
    data_thread.start()

    def _session_guard() -> None:
        """监控是否已到收盘退出时间。"""
        while not stop_event.is_set():
            now = now_provider()
            exit_at = _build_session_time(now, settings.SESSION_EXIT_TIME)
            if now >= exit_at:
                _shutdown(f"已到收盘退出时间 {settings.SESSION_EXIT_TIME}")
                return
            remaining = max(0.0, (exit_at - now).total_seconds())
            sleep_fn(min(max(int(settings.SESSION_POLL_INTERVAL_SEC), 1), remaining))

    threading.Thread(target=_session_guard, daemon=True, name="session-guard").start()

    logger.info("cytrade 运行中，将在 %s 自动退出。", settings.SESSION_EXIT_TIME)
    stop_event.wait()

    if data_thread and data_thread.is_alive():
        data_thread.join(timeout=2)

    logger.info("cytrade 已退出")


def run_daily_session(strategy_classes=None, settings: Settings = None,
                      now_provider=None, sleep_fn=None):
    """运行一个“交易日会话”。

    该模式面向“每天启动一次，收盘自动退出”的运行方式：

    1. 若当前是非交易日，则直接退出。
    2. 若当前早于 `SESSION_START_TIME`，则等待到启动时刻。
    3. 启动后优先恢复当日状态；若没有当日状态，则回退加载上一交易日状态。
    4. 到达 `SESSION_EXIT_TIME` 后自动保存状态并退出进程。

    运行说明：
        `run_daily_session()` 可以理解为“单次交易日会话的总控入口”。
        它不关心自己是在父进程里被手工调用，还是在 `ProcessPoolExecutor`
        创建的子进程中被调用；它只负责判断“今天是否应该运行、何时开始、何时结束”。

        它的职责主要是：

        - 先做交易日判断，非交易日不启动任何交易资源。
        - 若已经晚于收盘退出时间，则直接跳过。
        - 若还没到启动时刻，则等待到指定时间。
        - 时机成熟后，把控制权交给 `_run_managed_session()` 执行完整会话。
    """
    settings = settings or Settings()
    now_provider = now_provider or datetime.now
    sleep_fn = sleep_fn or time.sleep
    logger = get_logger("system")
    now = now_provider()

    if not is_market_day(now):
        logger.info("今日非交易日，跳过会话启动并直接退出")
        return "skipped_non_trading_day"

    exit_at = _build_session_time(now, settings.SESSION_EXIT_TIME)
    if now >= exit_at:
        logger.info("当前时间已超过收盘退出时间 %s，跳过本次会话", settings.SESSION_EXIT_TIME)
        return "skipped_after_close"

    stop_event = threading.Event()
    if not _wait_until_session_start(settings, stop_event, now_provider, sleep_fn):
        logger.info("会话尚未启动即收到停止信号")
        return "stopped_before_start"

    _run_managed_session(
        strategy_classes=strategy_classes,
        settings=settings,
        now_provider=now_provider,
        sleep_fn=sleep_fn,
    )
    return "completed"


def _run_daily_session_in_subprocess(strategy_specs: list[str], settings: Settings) -> str:
    """子进程入口：在独立进程中运行完整的交易日会话。

    运行说明：
        `ProcessPoolExecutor` 提交到子进程的参数必须可序列化，直接传递策略类对象
        在不同平台上并不总是可靠。因此父进程会先把策略类转换成 `module:Class`
        字符串，子进程拿到后再通过导入路径恢复为真实策略类。

        这样可以保证：
        - 父进程与子进程之间只传递简单可序列化参数。
        - 子进程启动后仍能准确恢复策略定义。
    """
    strategy_classes = _resolve_strategy_specs(strategy_specs)
    return run_daily_session(strategy_classes=strategy_classes, settings=settings)


def _launch_session_in_process(strategy_classes=None, settings: Settings = None) -> str:
    """为单次交易会话创建独立进程，并在任务结束后回收进程资源。

    运行说明：
        这个函数由父进程调度器触发，但真正的交易工作不会在父进程里执行。
        它只负责：

        1. 把策略类转换为可跨进程传输的文本描述。
        2. 临时创建一个 `ProcessPoolExecutor(max_workers=1)`。
        3. 向该执行器提交一次“交易日会话任务”。
        4. 等待子进程返回结果。
        5. 随着 `with ProcessPoolExecutor(...)` 退出，回收子进程资源。

        这样设计的关键意义在于：
        - 每个交易日任务都有自己独立的进程空间。
        - QMT 连接、行情订阅、线程、网络端口、内存状态不会长期留在父进程中。
        - 任务结束后，系统会自然回到一个轻量的“纯调度”状态。
    """
    settings = settings or Settings()
    strategy_specs = _normalize_strategy_specs(strategy_classes)
    logger = get_logger("system")
    logger.info("父进程准备启动子进程交易会话")

    # 每次任务都临时创建独立的进程池，只提交一个任务。
    # 这样任务结束后，子进程会随着执行器退出而被回收，不会长期残留。
    with ProcessPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_daily_session_in_subprocess, strategy_specs, settings)
        result = future.result()

    logger.info("子进程交易会话结束: %s", result)
    return result


def run_scheduler_service(strategy_classes=None, settings: Settings = None,
                          scheduler_cls=None) -> None:
    """启动父进程调度服务。

    父进程只负责：
    1. 使用 `BlockingScheduler` 维护计划任务。
    2. 在触发时通过 `ProcessPoolExecutor` 拉起独立子进程。

    真正的交易连接、行情订阅、策略运行、Web 服务、看门狗等，全部都在子进程中完成。

    运行说明：
        这是当前项目推荐的生产运行入口。
        当执行 `python main.py` 时，默认就是进入这个函数。

        该函数创建的是“父进程调度服务”，特点是：

        - 父进程本身不连接 QMT。
        - 父进程本身不启动行情订阅。
        - 父进程本身不运行策略。
        - 父进程只负责按照计划时间触发交易任务。

        这样一来，整个系统的长期运行职责被拆成了两层：

        - 父进程：长期稳定、轻量、负责调度。
        - 子进程：短生命周期、重资源、负责完整交易会话。

        如果你要理解“程序到底是怎么每天自动运行、收盘退出并回收资源的”，
        重点就看这里：

        - `scheduler.add_job(...)` 决定何时启动当日任务。
        - `_launch_session_in_process()` 决定如何创建独立子进程。
        - `_run_managed_session()` 决定子进程内部如何完整执行一次交易日会话。
    """
    from apscheduler.schedulers.blocking import BlockingScheduler

    settings = settings or Settings()
    logger = get_logger("system")
    scheduler_cls = scheduler_cls or BlockingScheduler
    scheduler = scheduler_cls()
    start_hour, start_minute = _parse_hhmm(settings.SESSION_START_TIME)
    strategy_specs = _normalize_strategy_specs(strategy_classes)

    scheduler.add_job(
        _launch_session_in_process,
        trigger="cron",
        day_of_week="mon-fri",
        hour=start_hour,
        minute=start_minute,
        id="daily_trading_session",
        args=[strategy_specs, settings],
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(int(settings.SESSION_POLL_INTERVAL_SEC), 60),
    )

    def _signal_handler(sig, frame):
        """响应父进程退出信号，停止调度器。"""
        logger.info("父进程收到退出信号 (%s)，正在停止调度器", sig)
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info(
        "父进程调度器已启动：交易任务将在每个工作日 %s 触发，并在独立进程中运行",
        settings.SESSION_START_TIME,
    )
    scheduler.start()


if __name__ == "__main__":
    from strategy.test_grid_strategy import TestGridStrategy
    run_scheduler_service(strategy_classes=[TestGridStrategy])
