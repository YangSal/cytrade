import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Settings
from main import build_app
from strategy.models import StrategyConfig
from strategy.test_grid_strategy import TestGridStrategy
from web.backend.main import init_app_context, run_server

try:
    from xtquant import xtdata
except ImportError:
    xtdata = None  # type: ignore


CODES = ["513050", "513180", "159981"]
BACKEND_URL = "http://127.0.0.1:8080"
FRONTEND_URL = "http://127.0.0.1:3000"
DEFAULT_KEEPALIVE = int(os.getenv("CYTRADE_SMOKETEST_KEEPALIVE", "5"))
LOT_SIZE = 100
INITIAL_LOTS = int(os.getenv("CYTRADE_SMOKETEST_INITIAL_LOTS", "100"))
GRID_LOTS = int(os.getenv("CYTRADE_SMOKETEST_GRID_LOTS", "1"))
INITIAL_QUANTITY = INITIAL_LOTS * LOT_SIZE
GRID_QUANTITY = GRID_LOTS * LOT_SIZE
MANUAL_STOP_ONLY = os.getenv("CYTRADE_SMOKETEST_MANUAL_STOP_ONLY", "1") != "0"


def to_xt(code: str) -> str:
    code = str(code).strip().zfill(6)
    if code.startswith(("5", "6")):
        return f"{code}.SH"
    return f"{code}.SZ"


def wait_for(predicate, timeout=30.0, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def wait_http(url: str, timeout: float = 30.0) -> bool:
    def _probe():
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return 200 <= resp.status < 500
        except Exception:
            return False

    return bool(wait_for(_probe, timeout=timeout, interval=0.5))


def fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_json(url: str):
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def get_reference_price(code: str) -> float:
    fallback = 1.2 if code.startswith("5") else 1.6
    if not xtdata:
        return fallback
    try:
        data = xtdata.get_full_tick([to_xt(code)]) or {}
        snapshot = data.get(to_xt(code)) or {}
        price = float(snapshot.get("lastPrice") or 0.0)
        return price if price > 0 else fallback
    except Exception:
        return fallback


def build_grid_params(price: float) -> dict:
    low = round(price * 0.997, 3)
    high = round(price * 1.003, 3)
    if high <= low:
        high = round(low + 0.006, 3)
    return {
        "grid_count": 6,
        "grid_low": low,
        "grid_high": high,
        "per_grid_amount": 300.0,
        "per_grid_quantity": GRID_QUANTITY,
    }


def start_frontend() -> subprocess.Popen | None:
    node = Path(r"C:\Program Files\nodejs\node.exe")
    vite = ROOT / "web" / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    if not node.exists() or not vite.exists():
        return None

    env = os.environ.copy()
    env["Path"] = str(node.parent) + os.pathsep + env.get("Path", "")
    log_path = ROOT / "_runtime_smoketest_three_symbols" / "frontend.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(node), str(vite), "--host", "127.0.0.1"],
        cwd=str(ROOT / "web" / "frontend"),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    proc._cytrade_log_file = log_file  # type: ignore[attr-defined]
    proc._cytrade_log_path = str(log_path)  # type: ignore[attr-defined]
    return proc


def stop_process(proc: subprocess.Popen | None):
    if not proc or proc.poll() is not None:
        if proc and hasattr(proc, "_cytrade_log_file"):
            try:
                proc._cytrade_log_file.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    finally:
        if hasattr(proc, "_cytrade_log_file"):
            try:
                proc._cytrade_log_file.close()  # type: ignore[attr-defined]
            except Exception:
                pass


def tail_file(path: str, limit: int = 2000) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        return text[-limit:]
    except Exception:
        return ""


def wait_buy_filled(order_mgr, strategy_id: str, min_quantity: int = 1):
    return wait_for(
        lambda: next(
            (
                o for o in reversed(order_mgr.get_orders_by_strategy(strategy_id))
                if o.direction.value == "BUY" and o.filled_quantity >= min_quantity
            ),
            None,
        ),
        timeout=45,
        interval=1,
    )


def seed_initial_position(strategy, trade_exec, order_mgr, price: float, quantity: int) -> dict:
    limit_price = round(price * 1.01, 3)
    initial = trade_exec.buy_limit(
        strategy.strategy_id,
        strategy.strategy_name,
        strategy.stock_code,
        limit_price,
        quantity,
        remark=f"smoketest initial position {INITIAL_LOTS} lots",
    )
    buy_filled = wait_buy_filled(order_mgr, strategy.strategy_id, min_quantity=quantity)
    if buy_filled:
        return {
            "method": "initial_limit_buy",
            "limit_price": limit_price,
            "filled_quantity": buy_filled.filled_quantity,
            "xt_order_id": buy_filled.xt_order_id,
        }

    if initial and initial.is_active():
        trade_exec.cancel_order(initial.order_uuid, remark="smoketest initial fallback cancel")
        time.sleep(2)

    fallback_price = round(price * 1.02, 3)
    fallback = trade_exec.buy_limit(
        strategy.strategy_id,
        strategy.strategy_name,
        strategy.stock_code,
        fallback_price,
        quantity,
        remark=f"smoketest fallback initial position {INITIAL_LOTS} lots",
    )
    buy_filled = wait_buy_filled(order_mgr, strategy.strategy_id, min_quantity=quantity)
    return {
        "method": "fallback_limit_buy",
        "fallback_price": fallback_price,
        "filled_quantity": getattr(buy_filled, "filled_quantity", 0),
        "xt_order_id": getattr(buy_filled or fallback, "xt_order_id", 0),
    }


def cancel_active_strategy_orders(strategy, trade_exec, order_mgr) -> list[dict]:
    canceled = []
    for order in order_mgr.get_orders_by_strategy(strategy.strategy_id):
        if order.is_active():
            ok = trade_exec.cancel_order(order.order_uuid, remark="smoketest pre-close cleanup")
            canceled.append({
                "order_uuid": order.order_uuid,
                "direction": order.direction.value,
                "quantity": order.quantity,
                "success": bool(ok),
            })
    if canceled:
        time.sleep(2)
    return canceled


def wait_sell_filled(order_mgr, strategy_id: str):
    return wait_for(
        lambda: next((o for o in order_mgr.get_orders_by_strategy(strategy_id)
                      if o.direction.value == "SELL" and o.filled_quantity > 0), None),
        timeout=30,
        interval=1,
    )


def wait_position_flat(pos_mgr, strategy_id: str):
    return wait_for(
        lambda: (lambda pos: pos is None or getattr(pos, "total_quantity", 0) <= 0)(
            pos_mgr.get_position(strategy_id)
        ),
        timeout=45,
        interval=1,
    )


def wait_close_order_filled(order_mgr, strategy_id: str, known_order_uuids: set[str]):
    return wait_for(
        lambda: next(
            (
                o for o in reversed(order_mgr.get_orders_by_strategy(strategy_id))
                if o.direction.value == "SELL"
                and o.order_uuid not in known_order_uuids
                and "Web 强制平仓" in (o.remark or "")
                and o.filled_quantity > 0
            ),
            None,
        ),
        timeout=45,
        interval=1,
    )


def wait_for_manual_stop(stop_event: threading.Event) -> None:
    print("smoke test running; press Ctrl+C to stop manually", flush=True)
    while not stop_event.wait(timeout=1):
        pass


def main():
    stop_event = threading.Event()

    def _handle_stop_signal(signum, frame):
        stop_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle_stop_signal)
            except (ValueError, OSError):
                pass

    runtime_dir = ROOT / "_runtime_smoketest_three_symbols"
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)

    settings = Settings(
        QMT_PATH=os.getenv("CYTRADE_QMT_PATH", r"D:\国金QMT交易端模拟\userdata_mini"),
        ACCOUNT_ID=os.getenv("CYTRADE_ACCOUNT_ID", "88011588"),
        ACCOUNT_TYPE=os.getenv("CYTRADE_ACCOUNT_TYPE", "STOCK"),
        SQLITE_DB_PATH=str(runtime_dir / "data" / "cytrade.db"),
        STATE_SAVE_DIR=str(runtime_dir / "saved_states"),
        LOG_DIR=str(runtime_dir / "logs"),
        WEB_HOST="127.0.0.1",
        WEB_PORT=8080,
        LOG_LEVEL="INFO",
        LOAD_PREVIOUS_STATE_ON_START=False,
    )

    ctx = build_app(strategy_classes=[], settings=settings)
    conn_mgr = ctx["conn_mgr"]
    runner = ctx["runner"]
    watchdog = ctx["watchdog"]
    data_sub = ctx["data_sub"]
    trade_exec = ctx["trade_exec"]
    pos_mgr = ctx["pos_mgr"]
    order_mgr = ctx["order_mgr"]
    data_mgr = ctx["data_mgr"]

    frontend_proc = None
    server_thread = None
    data_thread = None
    results = {
        "connect": False,
        "frontend": False,
        "backend": False,
        "strategies": [],
        "button_tests": {},
    }

    try:
        if not conn_mgr.connect():
            raise RuntimeError("QMT connection failed")
        results["connect"] = True
        data_mgr.clear_strategy_state()
        results["history_reset"] = True

        init_app_context(
            strategy_runner=runner,
            position_manager=pos_mgr,
            order_manager=order_mgr,
            data_manager=data_mgr,
            connection_manager=conn_mgr,
            trade_executor=trade_exec,
        )
        server_thread = run_server(host=settings.WEB_HOST, port=settings.WEB_PORT)
        if not server_thread:
            raise RuntimeError("backend server failed to start")
        results["backend"] = wait_http(f"{BACKEND_URL}/api/system/status", timeout=20)

        frontend_proc = start_frontend()
        if frontend_proc:
            root_ok = wait_http(FRONTEND_URL, timeout=45)
            strategies_js_ok = wait_http(f"{FRONTEND_URL}/src/views/Strategies.vue", timeout=10)
            results["frontend"] = bool(root_ok and strategies_js_ok)
            results["frontend_details"] = {
                "root_ok": root_ok,
                "strategies_view_ok": strategies_js_ok,
                "log_tail": tail_file(getattr(frontend_proc, "_cytrade_log_path", "")),
            }

        data_thread = threading.Thread(target=data_sub.start, daemon=True, name="data-sub")
        data_thread.start()

        strategies = []
        for code in CODES:
            price = get_reference_price(code)
            params = build_grid_params(price)
            strategy = TestGridStrategy(
                StrategyConfig(
                    stock_code=code,
                    max_position_amount=max(50000.0, round(price * (INITIAL_QUANTITY + GRID_QUANTITY * 20), 2)),
                    params=params,
                ),
                trade_exec,
                pos_mgr,
            )
            runner.add_strategy(strategy)
            strategies.append((strategy, price, params))

        for strategy, price, params in strategies:
            fill = seed_initial_position(strategy, trade_exec, order_mgr, price, INITIAL_QUANTITY)
            position = pos_mgr.get_position(strategy.strategy_id)
            results["strategies"].append({
                "strategy_id": strategy.strategy_id,
                "stock_code": strategy.stock_code,
                "reference_price": price,
                "grid_params": params,
                "buy": fill,
                "initial_lots": INITIAL_LOTS,
                "grid_lots": GRID_LOTS,
                "position_qty": getattr(position, "total_quantity", 0) if position else 0,
                "available_qty": getattr(position, "available_quantity", 0) if position else 0,
                "is_t0": getattr(position, "is_t0", False) if position else False,
            })

        watchdog.start()
        runner.start()
        watchdog.register_heartbeat("strategy_runner")

        listed = wait_for(lambda: fetch_json(f"{BACKEND_URL}/api/strategies"), timeout=20, interval=1)
        if not isinstance(listed, list) or len(listed) < 3:
            raise RuntimeError("strategies endpoint did not return all strategies")

        first_id = strategies[0][0].strategy_id
        pause_resp = post_json(f"{BACKEND_URL}/api/strategies/{first_id}/pause")
        paused = fetch_json(f"{BACKEND_URL}/api/strategies/{first_id}")
        resume_resp = post_json(f"{BACKEND_URL}/api/strategies/{first_id}/resume")
        resumed = fetch_json(f"{BACKEND_URL}/api/strategies/{first_id}")
        results["button_tests"]["pause_resume"] = {
            "pause_success": bool(pause_resp.get("success")),
            "paused_status": paused.get("status"),
            "resume_success": bool(resume_resp.get("success")),
            "resumed_status": resumed.get("status"),
        }
        results["button_tests"]["close"] = {
            "skipped": MANUAL_STOP_ONLY,
            "reason": "manual stop mode keeps strategies running until interrupted" if MANUAL_STOP_ONLY else "not skipped",
        }

        if results["frontend"]:
            page = fetch_text(FRONTEND_URL)
            strategies_view = fetch_text(f"{FRONTEND_URL}/src/views/Strategies.vue")
            results["frontend_checks"] = {
                "root_contains_dashboard": "cytrade" in page,
                "strategies_view_has_pause": "pause(row)" in strategies_view,
                "strategies_view_has_resume": "resume(row)" in strategies_view,
                "strategies_view_has_close": "close(row)" in strategies_view,
            }

        results["manual_stop_only"] = MANUAL_STOP_ONLY
        print(json.dumps(results, ensure_ascii=False, indent=2))

        if MANUAL_STOP_ONLY:
            wait_for_manual_stop(stop_event)
        else:
            time.sleep(DEFAULT_KEEPALIVE)
    finally:
        try:
            runner.stop()
        except Exception:
            pass
        try:
            watchdog.stop()
        except Exception:
            pass
        try:
            data_sub.stop()
        except Exception:
            pass
        try:
            conn_mgr.disconnect()
        except Exception:
            pass
        try:
            data_mgr.close()
        except Exception:
            pass
        stop_process(frontend_proc)


if __name__ == "__main__":
    main()
