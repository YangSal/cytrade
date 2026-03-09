"""
CyTrade2 主程序入口
启动顺序：
1. 初始化日志 / 配置 / 数据库
2. 连接 QMT
3. 初始化各模块
4. 注册回调链
5. 启动 Web 服务（后台线程）
6. 启动策略运行
7. 启动看门狗
8. 进入数据订阅阻塞循环 / APScheduler
"""
import sys
import os
import signal
import threading

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import Settings
from config.fee_schedule import FeeSchedule
from monitor.logger import LogManager, get_logger
from data.manager import DataManager
from core.connection import ConnectionManager
from core.callback import MyXtQuantTraderCallback
from core.data_subscription import DataSubscriptionManager
from trading.order_manager import OrderManager
from trading.executor import TradeExecutor
from position.manager import PositionManager
from strategy.runner import StrategyRunner
from monitor.watchdog import Watchdog


def build_app(strategy_classes=None, settings: Settings = None):
    """
    构建并连接所有模块。
    strategy_classes: 要运行的策略类列表
    """
    settings = settings or Settings()
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
    logger.info("CyTrade2 启动")

    # ---- 数据管理 ----
    data_mgr = DataManager(
        db_path=settings.SQLITE_DB_PATH,
        state_dir=settings.STATE_SAVE_DIR,
        remote_cfg=settings.REMOTE_DB_CONFIG,
    )
    if settings.ENABLE_REMOTE_DB:
        data_mgr.set_remote_enabled(True)

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
        strategy_classes=strategy_classes or [],
        latency_threshold_sec=settings.DATA_LATENCY_THRESHOLD_SEC,
        process_threshold_ms=settings.STRATEGY_PROCESS_THRESHOLD_MS,
    )

    # 注册订单 → 策略回调
    order_mgr.set_strategy_callback(runner.dispatch_order_update)

    # 重连后自动恢复行情订阅
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
    """启动主程序"""
    ctx = build_app(strategy_classes, settings)
    logger = get_logger("system")
    settings = ctx["settings"]
    conn_mgr = ctx["conn_mgr"]
    runner = ctx["runner"]
    watchdog = ctx["watchdog"]
    data_sub = ctx["data_sub"]

    # ---- 优雅退出 ----
    _stop_event = threading.Event()

    def _signal_handler(sig, frame):
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
    try:
        from web.backend.main import init_app_context, run_server
        from web.backend import routes as web_routes
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
            ctx["order_mgr"].set_trade_callback(web_routes._ws_manager.notify_trade_update)
    except Exception as e:
        logger.warning("Web 服务未启动（可能缺少 fastapi/uvicorn）: %s", e)

    # ---- 看门狗 ----
    watchdog.start()

    # ---- 策略启动 ----
    runner.start()
    watchdog.register_heartbeat("strategy_runner")

    # ---- 订阅阻塞（在子线程运行，主线程等待退出信号）----
    data_thread = threading.Thread(
        target=data_sub.start, daemon=True, name="data-sub"
    )
    data_thread.start()

    logger.info("CyTrade2 运行中。按 Ctrl+C 退出。")
    _stop_event.wait()
    logger.info("CyTrade2 已退出")


if __name__ == "__main__":
    from strategy.test_grid_strategy import TestGridStrategy
    run(strategy_classes=[TestGridStrategy])
