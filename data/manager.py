"""数据管理模块。

本模块把“运行中的业务对象”转换为“可保存、可恢复、可查询的数据”：
1. 本地 SQLite：保存订单、成交、策略盈亏历史。
2. pickle 状态文件：用于跨交易日恢复策略运行状态。
3. 可选 PostgreSQL：用于远程同步当天成交数据。
"""
import os
import pickle
import json
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.trading_calendar import minus_one_market_day
from monitor.logger import get_logger

logger = get_logger("system")

# ---- DDL ----------------------------------------------------------------
_DDL = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_type INTEGER DEFAULT 0,
    account_id   TEXT DEFAULT '',
    order_type   INTEGER DEFAULT 0,
    trade_id     TEXT,
    traded_time  INTEGER DEFAULT 0,
    order_uuid   TEXT NOT NULL,
    xt_order_id  INTEGER,
    order_sysid  TEXT DEFAULT '',
    strategy_name TEXT NOT NULL,
    strategy_id  TEXT NOT NULL,
    order_remark TEXT DEFAULT '',
    stock_code   TEXT NOT NULL,
    direction    TEXT NOT NULL,
    xt_direction INTEGER DEFAULT 0,
    offset_flag  INTEGER DEFAULT 0,
    quantity     INTEGER NOT NULL,
    price        REAL NOT NULL,
    amount       REAL NOT NULL,
    commission   REAL DEFAULT 0,
    buy_commission REAL DEFAULT 0,
    sell_commission REAL DEFAULT 0,
    stamp_tax    REAL DEFAULT 0,
    total_fee    REAL DEFAULT 0,
    is_t0        INTEGER DEFAULT 0,
    remark       TEXT,
    trade_time   TIMESTAMP,
    create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_uuid      TEXT NOT NULL UNIQUE,
    xt_order_id     INTEGER,
    account_type    INTEGER DEFAULT 0,
    account_id      TEXT DEFAULT '',
    strategy_name   TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    stock_code      TEXT NOT NULL,
    xt_stock_code   TEXT DEFAULT '',
    direction       TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    xt_order_type   INTEGER DEFAULT 0,
    price_type      INTEGER DEFAULT 0,
    price           REAL,
    quantity        INTEGER,
    amount          REAL,
    status          TEXT NOT NULL,
    xt_order_status INTEGER DEFAULT 0,
    status_msg      TEXT DEFAULT '',
    order_sysid     TEXT DEFAULT '',
    order_time      INTEGER DEFAULT 0,
    xt_direction    INTEGER DEFAULT 0,
    offset_flag     INTEGER DEFAULT 0,
    secu_account    TEXT DEFAULT '',
    instrument_name TEXT DEFAULT '',
    filled_quantity INTEGER DEFAULT 0,
    filled_amount   REAL DEFAULT 0,
    filled_avg_price REAL DEFAULT 0,
    commission      REAL DEFAULT 0,
    buy_commission  REAL DEFAULT 0,
    sell_commission REAL DEFAULT 0,
    stamp_tax       REAL DEFAULT 0,
    total_fee       REAL DEFAULT 0,
    remark          TEXT,
    xt_order_snapshot TEXT DEFAULT '',
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_pnl_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name     TEXT NOT NULL,
    strategy_id       TEXT NOT NULL,
    stock_code        TEXT NOT NULL,
    total_buy_amount  REAL DEFAULT 0,
    total_sell_amount REAL DEFAULT 0,
    total_profit      REAL DEFAULT 0,
    total_commission  REAL DEFAULT 0,
    start_time        TIMESTAMP,
    end_time          TIMESTAMP,
    create_time       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trades_strategy_id  ON trades(strategy_id);
CREATE INDEX IF NOT EXISTS idx_trades_order_uuid   ON trades(order_uuid);
CREATE INDEX IF NOT EXISTS idx_orders_strategy_id  ON orders(strategy_id);
CREATE INDEX IF NOT EXISTS idx_orders_status       ON orders(status);
"""


class DataManager:
    """数据持久化管理。

    它统一负责三类数据：
    1. SQLite 中的订单、成交、策略盈亏历史。
    2. pickle 保存的策略运行快照。
    3. 可选的 PostgreSQL 远程同步。
    """

    def __init__(self, db_path: str = "./data/db/cytrade.db",
                 state_dir: str = "./saved_states",
                 remote_cfg: Optional[Dict] = None):
        """初始化数据管理器。

        Args:
            db_path: SQLite 数据库文件路径。
            state_dir: 策略状态快照目录。
            remote_cfg: 可选的远程 PostgreSQL 配置字典。
        """
        # ``_db_path`` 是本地 SQLite 文件路径。
        self._db_path = db_path
        # ``_state_dir`` 是保存 pickle 快照的目录。
        self._state_dir = state_dir
        # ``_remote_cfg`` 保存远程数据库连接配置。
        self._remote_cfg = remote_cfg
        # ``_lock`` 保护数据库操作，减少多线程竞争。
        self._lock = threading.Lock()
        # ``_remote_enabled`` 标记是否启用远程同步能力。
        self._remote_enabled = False
        # ``_pg_conn`` 保存可选的 PostgreSQL 连接对象。
        self._pg_conn = None

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs(state_dir, exist_ok=True)
        self.init_db()

    # ------------------------------------------------------------------ SQLite

    def init_db(self) -> None:
        """初始化数据库表结构，并执行必要的兼容迁移。

        这里会先创建最新表结构，再对历史数据库执行增量迁移，
        以尽量保证老数据文件仍可被当前版本读取。
        """
        try:
            with self._get_conn() as conn:
                conn.executescript(_DDL)
                self._migrate_xt_order_id_columns(conn)
                self._migrate_trade_extra_columns(conn)
                self._migrate_order_extra_columns(conn)
            logger.info("DataManager: SQLite 初始化完成 — %s", self._db_path)
        except Exception as e:
            logger.error("DataManager: 初始化数据库失败: %s", e, exc_info=True)
            raise

    def save_trade(self, trade) -> None:
        """保存一条成交记录到 SQLite。"""
        sql = """
        INSERT INTO trades
          (account_type, account_id, order_type, trade_id, traded_time,
           order_uuid, xt_order_id, order_sysid, strategy_name, strategy_id,
           order_remark, stock_code, direction, xt_direction, offset_flag,
              quantity, price, amount, commission, buy_commission, sell_commission,
              stamp_tax, total_fee, is_t0, trade_time)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        params = (
            int(getattr(trade, "account_type", 0) or 0),
            str(getattr(trade, "account_id", "") or ""),
            int(getattr(trade, "order_type", 0) or 0),
            trade.trade_id,
            int(getattr(trade, "xt_traded_time", 0) or 0),
            trade.order_uuid,
            int(trade.xt_order_id or 0),
            str(getattr(trade, "order_sysid", "") or ""),
            trade.strategy_name,
            trade.strategy_id,
            str(getattr(trade, "order_remark", "") or ""),
            trade.stock_code,
            str(trade.direction.value),
            int(getattr(trade, "xt_direction", 0) or 0),
            int(getattr(trade, "offset_flag", 0) or 0),
            trade.quantity,
            trade.price,
            trade.amount,
            trade.commission,
            float(getattr(trade, "buy_commission", 0.0) or 0.0),
            float(getattr(trade, "sell_commission", 0.0) or 0.0),
            float(getattr(trade, "stamp_tax", 0.0) or 0.0),
            float(getattr(trade, "total_fee", 0.0) or 0.0),
            1 if bool(getattr(trade, "is_t0", False)) else 0,
            self._to_yyyymmdd(trade.trade_time)
        )
        self._execute(sql, params)

    def save_order(self, order) -> None:
        """新增或更新订单记录。

        这里使用 ``ON CONFLICT``，这样同一订单既能插入也能更新，
        适合订单状态不断变化的场景。
        """
        sql = """
        INSERT INTO orders
                    (order_uuid, xt_order_id, account_type, account_id, strategy_name, strategy_id,
                     stock_code, xt_stock_code, direction, order_type, xt_order_type, price_type,
                     price, quantity, amount, status, xt_order_status, status_msg, order_sysid,
                     order_time, xt_direction, offset_flag, secu_account, instrument_name,
                     filled_quantity, filled_amount, filled_avg_price, commission, buy_commission,
                     sell_commission, stamp_tax, total_fee, remark, xt_order_snapshot)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(order_uuid) DO UPDATE SET
          xt_order_id     = excluded.xt_order_id,
                    account_type    = excluded.account_type,
                    account_id      = excluded.account_id,
                    xt_stock_code   = excluded.xt_stock_code,
          status          = excluded.status,
                    xt_order_status = excluded.xt_order_status,
                    status_msg      = excluded.status_msg,
                    order_sysid     = excluded.order_sysid,
                    order_time      = excluded.order_time,
                    xt_order_type   = excluded.xt_order_type,
                    price_type      = excluded.price_type,
                    xt_direction    = excluded.xt_direction,
                    offset_flag     = excluded.offset_flag,
                    secu_account    = excluded.secu_account,
                    instrument_name = excluded.instrument_name,
          filled_quantity = excluded.filled_quantity,
          filled_amount   = excluded.filled_amount,
          filled_avg_price= excluded.filled_avg_price,
          commission      = excluded.commission,
                    buy_commission  = excluded.buy_commission,
                    sell_commission = excluded.sell_commission,
                    stamp_tax       = excluded.stamp_tax,
                    total_fee       = excluded.total_fee,
                    xt_order_snapshot = excluded.xt_order_snapshot,
          update_time     = CURRENT_TIMESTAMP
        """
        params = (
                        order.order_uuid,
                        int(order.xt_order_id or 0),
                        int(getattr(order, "account_type", 0) or 0),
                        str(getattr(order, "account_id", "") or ""),
                        order.strategy_name,
                        order.strategy_id,
                        order.stock_code,
                        str(getattr(order, "xt_stock_code", "") or ""),
                        str(order.direction.value),
                        str(order.order_type.value),
                        int(getattr(order, "xt_order_type", 0) or 0),
                        int(getattr(order, "price_type", 0) or 0),
                        order.price,
                        order.quantity,
                        order.amount,
                        str(order.status.value),
                        int(getattr(order, "xt_order_status", 0) or 0),
                        str(getattr(order, "status_msg", "") or ""),
                        str(getattr(order, "order_sysid", "") or ""),
                        int(getattr(order, "order_time", 0) or 0),
                        int(getattr(order, "xt_direction", 0) or 0),
                        int(getattr(order, "offset_flag", 0) or 0),
                        str(getattr(order, "secu_account", "") or ""),
                        str(getattr(order, "instrument_name", "") or ""),
                        order.filled_quantity,
                        order.filled_amount,
                        order.filled_avg_price,
                        order.commission,
                        float(getattr(order, "buy_commission", 0.0) or 0.0),
                        float(getattr(order, "sell_commission", 0.0) or 0.0),
                        float(getattr(order, "stamp_tax", 0.0) or 0.0),
                        float(getattr(order, "total_fee", 0.0) or 0.0),
                        order.remark,
                        self._json_dumps(getattr(order, "xt_fields", {}) or {}),
        )
        self._execute(sql, params)

    def save_strategy_pnl(self, strategy_id: str, strategy_name: str,
                          stock_code: str, pnl_info: Dict) -> None:
        """保存策略盈亏历史（策略结束后调用）"""
        sql = """
        INSERT INTO strategy_pnl_history
          (strategy_name, strategy_id, stock_code, total_buy_amount,
           total_sell_amount, total_profit, total_commission, start_time, end_time)
        VALUES (?,?,?,?,?,?,?,?,?)
        """
        params = (
            strategy_name, strategy_id, stock_code,
            pnl_info.get("total_buy_amount", 0),
            pnl_info.get("total_sell_amount", 0),
            pnl_info.get("total_profit", 0),
            pnl_info.get("total_commission", 0),
            self._normalize_date_value(pnl_info.get("start_time", "")),
            self._normalize_date_value(pnl_info.get("end_time", datetime.now())),
        )
        self._execute(sql, params)

    def query_trades(self, strategy_id: Optional[str] = None,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> List[Dict]:
        """按条件查询成交记录。

        Returns:
            由普通字典组成的列表，便于直接给 API 层使用。
        """
        clauses = []
        params: list = []
        if strategy_id:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if start_date:
            clauses.append("trade_time >= ?")
            params.append(self._normalize_date_value(start_date))
        if end_date:
            clauses.append("trade_time <= ?")
            params.append(self._normalize_date_value(end_date))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM trades {where} ORDER BY trade_time DESC"
        return self._fetchall(sql, params)

    def query_orders(self, strategy_id: Optional[str] = None,
                     status: Optional[str] = None) -> List[Dict]:
        """按条件查询订单记录。"""
        clauses = []
        params: list = []
        if strategy_id:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM orders {where} ORDER BY create_time DESC"
        return self._fetchall(sql, params)

    # ------------------------------------------------------------------ Pickle 状态

    def save_strategy_state(self, snapshots: list, trading_day: Optional[str] = None) -> None:
        """将策略快照列表序列化到 pickle 文件。

        说明：该状态文件仅用于本项目内部的跨交易日恢复，
        不保证跨大版本代码结构变更后的兼容性。

        Args:
            snapshots: 需要保存的策略快照列表。
            trading_day: 目标交易日，格式为 ``YYYYMMDD``；为空时使用今日日期。
        """
        path = self._state_file(trading_day)
        try:
            with open(path, "wb") as f:
                pickle.dump(snapshots, f)
            logger.info("DataManager: 策略状态已保存 → %s (%d 条)", path, len(snapshots))
        except Exception as e:
            logger.error("DataManager: 保存策略状态失败: %s", e, exc_info=True)

    def load_strategy_state(self, trading_day: Optional[str] = None,
                            fallback_previous_market_day: bool = True) -> Optional[list]:
        """加载策略快照列表。

        默认会优先尝试加载“今日快照”；如果今日文件不存在，则继续回退到上一个
        交易日的快照文件。这能满足以下两种常见场景：

        1. 盘中异常重启：优先恢复当日最新状态。
        2. 次日开盘启动：自动恢复上一交易日收盘后保存的状态。

        Args:
            trading_day: 目标交易日，格式为 ``YYYYMMDD``；为空时使用今日日期。
            fallback_previous_market_day: 当目标日期文件不存在时，是否继续尝试上一交易日。

        Returns:
            Optional[list]: 加载到的快照列表；若未找到可用状态文件则返回 ``None``。
        """
        target_day = trading_day or datetime.now().strftime("%Y%m%d")
        candidate_days = [target_day]

        if fallback_previous_market_day:
            try:
                previous_day = minus_one_market_day(target_day)
                if previous_day not in candidate_days:
                    candidate_days.append(previous_day)
            except Exception as exc:
                logger.warning("DataManager: 计算上一交易日失败，跳过回退加载: %s", exc)

        for day in candidate_days:
            path = self._state_file(day)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "rb") as f:
                    snapshots = pickle.load(f)
                logger.info("DataManager: 加载策略状态 ← %s (%d 条)", path, len(snapshots))
                return snapshots
            except Exception as e:
                logger.error("DataManager: 加载策略状态失败: %s", e, exc_info=True)
                return None

        return None

    def clear_strategy_state(self, trading_day: Optional[str] = None) -> None:
        """删除指定交易日的策略状态快照文件。"""
        path = self._state_file(trading_day)
        if os.path.exists(path):
            os.remove(path)

    def close(self) -> None:
        """释放数据管理器持有的外部资源。"""
        if self._pg_conn is not None:
            try:
                self._pg_conn.close()
            except Exception as exc:
                logger.warning("DataManager: 关闭 PostgreSQL 连接时异常: %s", exc)
            finally:
                self._pg_conn = None

    # ------------------------------------------------------------------ 远程 PostgreSQL

    def set_remote_enabled(self, enabled: bool) -> None:
        """开启或关闭远程数据库同步能力。"""
        self._remote_enabled = enabled
        if enabled:
            self._connect_pg()

    def sync_to_remote(self) -> None:
        """将本地 SQLite 数据同步到远程 PostgreSQL（可选功能）"""
        if not self._remote_enabled or not self._pg_conn:
            return
        try:
            self._do_sync()
        except Exception as e:
            logger.error("DataManager: 远程同步失败: %s", e, exc_info=True)

    # ------------------------------------------------------------------ Private

    def _get_conn(self) -> sqlite3.Connection:
        """创建一个新的 SQLite 连接。

        这里每次都返回新连接，配合线程锁使用，
        能减少跨线程共用连接带来的问题。
        """
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _execute(self, sql: str, params: tuple = ()) -> None:
        """执行写入类 SQL。

        所有写操作都串行化执行，避免多个线程同时写 SQLite 时出现锁竞争问题。
        """
        with self._lock:
            try:
                with self._get_conn() as conn:
                    conn.execute(sql, params)
                    conn.commit()
            except Exception as e:
                logger.error("DataManager SQL 执行失败: %s | %s", sql[:80], e, exc_info=True)
                raise

    def _fetchall(self, sql: str, params: list = ()) -> List[Dict]:
        """执行查询类 SQL，并把结果转成普通字典列表。"""
        with self._lock:
            try:
                with self._get_conn() as conn:
                    rows = conn.execute(sql, params).fetchall()
                    return [dict(r) for r in rows]
            except Exception as e:
                logger.error("DataManager 查询失败: %s | %s", sql[:80], e, exc_info=True)
                return []

    def _state_file(self, trading_day: Optional[str] = None) -> str:
        """返回指定交易日的策略状态快照文件路径。"""
        target_day = str(trading_day or datetime.now().strftime("%Y%m%d"))
        return os.path.join(self._state_dir, f"strategy_state_{target_day}.pkl")

    def _connect_pg(self) -> None:
        """尝试连接远程 PostgreSQL。"""
        if not self._remote_cfg or not self._remote_cfg.get("host"):
            logger.warning("DataManager: 远程数据库未配置 host，跳过连接")
            return
        try:
            import psycopg2
            self._pg_conn = psycopg2.connect(**{
                k: v for k, v in self._remote_cfg.items()
                if k in ("host", "port", "dbname", "user", "password") and v
            })
            logger.info("DataManager: 已连接远程 PostgreSQL")
        except Exception as e:
            logger.error("DataManager: PostgreSQL 连接失败: %s", e, exc_info=True)
            self._pg_conn = None

    def _do_sync(self) -> None:
        """执行一次简单远程同步：把今日成交写入 PostgreSQL。"""
        today = datetime.now().strftime("%Y%m%d")
        trades = self.query_trades(start_date=today)
        if not trades:
            return
        cur = self._pg_conn.cursor()
        upsert_sql = """
        INSERT INTO trades
          (trade_id, order_uuid, xt_order_id, strategy_name, strategy_id,
           stock_code, direction, quantity, price, amount, commission, trade_time)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (trade_id) DO NOTHING
        """
        for t in trades:
            cur.execute(upsert_sql, (
                t["trade_id"], t["order_uuid"], t["xt_order_id"],
                t["strategy_name"], t["strategy_id"], t["stock_code"],
                t["direction"], t["quantity"], t["price"],
                t["amount"], t["commission"], t["trade_time"],
            ))
        self._pg_conn.commit()
        logger.info("DataManager: 同步 %d 条成交到远程数据库", len(trades))

    @staticmethod
    def _migrate_xt_order_id_columns(conn: sqlite3.Connection) -> None:
        """将历史 TEXT 类型的 xt_order_id 列迁移为 INTEGER。"""
        def _column_type(table_name: str, column_name: str) -> str:
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            for row in rows:
                if row[1] == column_name:
                    return str(row[2]).upper()
            return ""

        if _column_type("trades", "xt_order_id") == "INTEGER" and \
           _column_type("orders", "xt_order_id") == "INTEGER":
            return

        conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id     TEXT,
            order_uuid   TEXT NOT NULL,
            xt_order_id  INTEGER,
            strategy_name TEXT NOT NULL,
            strategy_id  TEXT NOT NULL,
            stock_code   TEXT NOT NULL,
            direction    TEXT NOT NULL,
            quantity     INTEGER NOT NULL,
            price        REAL NOT NULL,
            amount       REAL NOT NULL,
            commission   REAL DEFAULT 0,
            remark       TEXT,
            trade_time   TIMESTAMP,
            create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        INSERT INTO trades_new (
            id, trade_id, order_uuid, xt_order_id, strategy_name, strategy_id,
            stock_code, direction, quantity, price, amount, commission, remark,
            trade_time, create_time
        )
        SELECT
            id, trade_id, order_uuid,
            CASE WHEN xt_order_id IS NULL OR xt_order_id = '' THEN 0 ELSE CAST(xt_order_id AS INTEGER) END,
            strategy_name, strategy_id, stock_code, direction, quantity, price,
            amount, commission, remark, trade_time, create_time
        FROM trades;

        DROP TABLE trades;
        ALTER TABLE trades_new RENAME TO trades;

        CREATE TABLE IF NOT EXISTS orders_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_uuid      TEXT NOT NULL UNIQUE,
            xt_order_id     INTEGER,
            strategy_name   TEXT NOT NULL,
            strategy_id     TEXT NOT NULL,
            stock_code      TEXT NOT NULL,
            direction       TEXT NOT NULL,
            order_type      TEXT NOT NULL,
            price           REAL,
            quantity        INTEGER,
            amount          REAL,
            status          TEXT NOT NULL,
            filled_quantity INTEGER DEFAULT 0,
            filled_amount   REAL DEFAULT 0,
            filled_avg_price REAL DEFAULT 0,
            commission      REAL DEFAULT 0,
            remark          TEXT,
            create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        INSERT INTO orders_new (
            id, order_uuid, xt_order_id, strategy_name, strategy_id, stock_code,
            direction, order_type, price, quantity, amount, status,
            filled_quantity, filled_amount, filled_avg_price, commission,
            remark, create_time, update_time
        )
        SELECT
            id, order_uuid,
            CASE WHEN xt_order_id IS NULL OR xt_order_id = '' THEN 0 ELSE CAST(xt_order_id AS INTEGER) END,
            strategy_name, strategy_id, stock_code, direction, order_type,
            price, quantity, amount, status, filled_quantity, filled_amount,
            filled_avg_price, commission, remark, create_time, update_time
        FROM orders;

        DROP TABLE orders;
        ALTER TABLE orders_new RENAME TO orders;

        CREATE INDEX IF NOT EXISTS idx_trades_strategy_id  ON trades(strategy_id);
        CREATE INDEX IF NOT EXISTS idx_trades_order_uuid   ON trades(order_uuid);
        CREATE INDEX IF NOT EXISTS idx_orders_strategy_id  ON orders(strategy_id);
        CREATE INDEX IF NOT EXISTS idx_orders_status       ON orders(status);
        """)

    @staticmethod
    def _migrate_trade_extra_columns(conn: sqlite3.Connection) -> None:
        """为历史 `trades` 表补齐 XtTrade 扩展字段。"""
        rows = conn.execute("PRAGMA table_info(trades)").fetchall()
        existing = {str(row[1]).lower() for row in rows}
        required_columns = {
            "account_type": "INTEGER DEFAULT 0",
            "account_id": "TEXT DEFAULT ''",
            "order_type": "INTEGER DEFAULT 0",
            "traded_time": "INTEGER DEFAULT 0",
            "order_sysid": "TEXT DEFAULT ''",
            "order_remark": "TEXT DEFAULT ''",
            "xt_direction": "INTEGER DEFAULT 0",
            "offset_flag": "INTEGER DEFAULT 0",
            "buy_commission": "REAL DEFAULT 0",
            "sell_commission": "REAL DEFAULT 0",
            "stamp_tax": "REAL DEFAULT 0",
            "total_fee": "REAL DEFAULT 0",
            "is_t0": "INTEGER DEFAULT 0",
        }
        for name, ddl in required_columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {name} {ddl}")

    @staticmethod
    def _migrate_order_extra_columns(conn: sqlite3.Connection) -> None:
        """为历史 `orders` 表补齐 XtOrder 扩展字段。"""
        rows = conn.execute("PRAGMA table_info(orders)").fetchall()
        existing = {str(row[1]).lower() for row in rows}
        required_columns = {
            "account_type": "INTEGER DEFAULT 0",
            "account_id": "TEXT DEFAULT ''",
            "xt_stock_code": "TEXT DEFAULT ''",
            "xt_order_type": "INTEGER DEFAULT 0",
            "price_type": "INTEGER DEFAULT 0",
            "xt_order_status": "INTEGER DEFAULT 0",
            "status_msg": "TEXT DEFAULT ''",
            "order_sysid": "TEXT DEFAULT ''",
            "order_time": "INTEGER DEFAULT 0",
            "xt_direction": "INTEGER DEFAULT 0",
            "offset_flag": "INTEGER DEFAULT 0",
            "secu_account": "TEXT DEFAULT ''",
            "instrument_name": "TEXT DEFAULT ''",
            "buy_commission": "REAL DEFAULT 0",
            "sell_commission": "REAL DEFAULT 0",
            "stamp_tax": "REAL DEFAULT 0",
            "total_fee": "REAL DEFAULT 0",
            "xt_order_snapshot": "TEXT DEFAULT ''",
        }
        for name, ddl in required_columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {ddl}")

    @staticmethod
    def _to_yyyymmdd(value) -> str:
        """把各种日期表示统一转成 ``YYYYMMDD``。"""
        if isinstance(value, datetime):
            return value.strftime("%Y%m%d")
        return DataManager._normalize_date_value(value)

    @staticmethod
    def _normalize_date_value(value) -> str:
        """把日期值清洗成适合数据库比较的字符串格式。

        目标格式统一为 `YYYYMMDD`，便于按字符串直接比较。
        """
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.strftime("%Y%m%d")

        raw = str(value).strip()
        if not raw:
            return ""

        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) >= 8:
            return digits[:8]
        return raw

    @staticmethod
    def _json_dumps(value) -> str:
        """把复杂对象安全序列化为 JSON 文本。"""
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return "{}"


__all__ = ["DataManager"]
