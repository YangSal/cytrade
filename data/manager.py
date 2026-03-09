"""
数据管理模块
1. 本地 SQLite：交易记录、订单记录、策略盈亏历史
2. 策略状态序列化（pickle）：跨交易日恢复
3. 远程 PostgreSQL 同步（可选）
"""
import os
import pickle
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

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
    """数据持久化管理（SQLite + pickle；可选 PostgreSQL）"""

    def __init__(self, db_path: str = "./data/db/cytrade2.db",
                 state_dir: str = "./saved_states",
                 remote_cfg: Optional[Dict] = None):
        self._db_path = db_path
        self._state_dir = state_dir
        self._remote_cfg = remote_cfg
        self._lock = threading.Lock()
        self._remote_enabled = False
        self._pg_conn = None

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs(state_dir, exist_ok=True)
        self.init_db()

    # ------------------------------------------------------------------ SQLite

    def init_db(self) -> None:
        """初始化数据库表结构"""
        try:
            with self._get_conn() as conn:
                conn.executescript(_DDL)
                self._migrate_xt_order_id_columns(conn)
                self._migrate_trade_extra_columns(conn)
            logger.info("DataManager: SQLite 初始化完成 — %s", self._db_path)
        except Exception as e:
            logger.error("DataManager: 初始化数据库失败: %s", e, exc_info=True)
            raise

    def save_trade(self, trade) -> None:
        """保存成交记录"""
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
        """新增或更新订单记录"""
        sql = """
        INSERT INTO orders
          (order_uuid, xt_order_id, strategy_name, strategy_id, stock_code,
           direction, order_type, price, quantity, amount, status,
           filled_quantity, filled_amount, filled_avg_price, commission, remark)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(order_uuid) DO UPDATE SET
          xt_order_id     = excluded.xt_order_id,
          status          = excluded.status,
          filled_quantity = excluded.filled_quantity,
          filled_amount   = excluded.filled_amount,
          filled_avg_price= excluded.filled_avg_price,
          commission      = excluded.commission,
          update_time     = CURRENT_TIMESTAMP
        """
        params = (
            order.order_uuid, int(order.xt_order_id or 0),
            order.strategy_name, order.strategy_id, order.stock_code,
            str(order.direction.value), str(order.order_type.value),
            order.price, order.quantity, order.amount,
            str(order.status.value), order.filled_quantity,
            order.filled_amount, order.filled_avg_price,
            order.commission, order.remark
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
        """查询成交记录"""
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
        """查询订单"""
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

    def save_strategy_state(self, snapshots: list) -> None:
        """将策略快照列表序列化到 pickle 文件。

        说明：该状态文件仅用于本项目内部的跨交易日恢复，
        不保证跨大版本代码结构变更后的兼容性。
        """
        path = self._state_file()
        try:
            with open(path, "wb") as f:
                pickle.dump(snapshots, f)
            logger.info("DataManager: 策略状态已保存 → %s (%d 条)", path, len(snapshots))
        except Exception as e:
            logger.error("DataManager: 保存策略状态失败: %s", e, exc_info=True)

    def load_strategy_state(self) -> Optional[list]:
        """加载策略快照列表，无文件时返回 None"""
        path = self._state_file()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                snapshots = pickle.load(f)
            logger.info("DataManager: 加载策略状态 ← %s (%d 条)", path, len(snapshots))
            return snapshots
        except Exception as e:
            logger.error("DataManager: 加载策略状态失败: %s", e, exc_info=True)
            return None

    def clear_strategy_state(self) -> None:
        """清除保存的策略状态文件"""
        path = self._state_file()
        if os.path.exists(path):
            os.remove(path)

    # ------------------------------------------------------------------ 远程 PostgreSQL

    def set_remote_enabled(self, enabled: bool) -> None:
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
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            try:
                with self._get_conn() as conn:
                    conn.execute(sql, params)
                    conn.commit()
            except Exception as e:
                logger.error("DataManager SQL 执行失败: %s | %s", sql[:80], e, exc_info=True)
                raise

    def _fetchall(self, sql: str, params: list = ()) -> List[Dict]:
        with self._lock:
            try:
                with self._get_conn() as conn:
                    rows = conn.execute(sql, params).fetchall()
                    return [dict(r) for r in rows]
            except Exception as e:
                logger.error("DataManager 查询失败: %s | %s", sql[:80], e, exc_info=True)
                return []

    def _state_file(self) -> str:
        from datetime import date
        today = date.today().strftime("%Y%m%d")
        return os.path.join(self._state_dir, f"strategy_state_{today}.pkl")

    def _connect_pg(self) -> None:
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
        """简单同步：将今日成交记录写入远程数据库"""
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
        """为历史 trades 表补齐 XtTrade 扩展字段。"""
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
    def _to_yyyymmdd(value) -> str:
        if isinstance(value, datetime):
            return value.strftime("%Y%m%d")
        return DataManager._normalize_date_value(value)

    @staticmethod
    def _normalize_date_value(value) -> str:
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


__all__ = ["DataManager"]
