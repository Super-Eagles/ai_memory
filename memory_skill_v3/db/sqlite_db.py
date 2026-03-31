import os
import sqlite3
import threading

import sqlite_vec

from .. import config

# ── 连接单例 ──────────────────────────────────────────────────────────────────
# SQLite 本身支持多线程读，但写操作存在竞争。
# write_lock 在同一进程内序列化所有写入，配合 check_same_thread=False 使用。
# 跨进程并发写入已通过 WAL 模式（PRAGMA journal_mode=WAL）支持，见 get_conn()。
_conn: sqlite3.Connection | None = None
write_lock = threading.Lock()

# 将相对路径解析为 skill 包目录下的绝对路径，
# 避免因调用方工作目录不同而导致数据库分散在多处。
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH   = (
    config.SQLITE_PATH
    if os.path.isabs(config.SQLITE_PATH)
    else os.path.join(_SKILL_DIR, config.SQLITE_PATH)
)


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        db_dir = os.path.dirname(_DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row

        # WAL 模式允许多进程并发读写，写操作不阻塞读操作。
        # busy_timeout 避免多 worker 同时写时立即抛 OperationalError。
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=3000")

        _conn.enable_load_extension(True)
        sqlite_vec.load(_conn)
        _conn.enable_load_extension(False)

        _create_schema(_conn)
        print(f"[memory-skill] DB path: {_DB_PATH}")
    return _conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            session_id  TEXT NOT NULL,
            turn        INTEGER NOT NULL,
            item_index  INTEGER DEFAULT 0,
            kind        TEXT DEFAULT 'general',
            summary     TEXT NOT NULL,
            keywords    TEXT DEFAULT '[]',
            raw_q       TEXT,
            raw_a       TEXT,
            version     INTEGER DEFAULT 1,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    _ensure_columns(conn)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_user    ON memories(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_session ON memories(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_time    ON memories(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_turn    ON memories(session_id, turn, item_index)")

    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(summary, keywords, content='memories', content_rowid='rowid')
    """)

    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS fts_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, summary, keywords)
            VALUES (new.rowid, new.summary, new.keywords);
        END;

        CREATE TRIGGER IF NOT EXISTS fts_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, summary, keywords)
            VALUES ('delete', old.rowid, old.summary, old.keywords);
        END;

        CREATE TRIGGER IF NOT EXISTS fts_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, summary, keywords)
            VALUES ('delete', old.rowid, old.summary, old.keywords);
            INSERT INTO memories_fts(rowid, summary, keywords)
            VALUES (new.rowid, new.summary, new.keywords);
        END;
    """)

    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec
            USING vec0(embedding float[{config.EMBED_DIM}])
    """)

    conn.commit()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(memories)").fetchall()
    columns = {row["name"] for row in rows}

    if "item_index" not in columns:
        conn.execute("ALTER TABLE memories ADD COLUMN item_index INTEGER DEFAULT 0")

    if "kind" not in columns:
        conn.execute("ALTER TABLE memories ADD COLUMN kind TEXT DEFAULT 'general'")


def get_db_path() -> str:
    """返回数据库文件的绝对路径。"""
    return _DB_PATH


def close() -> None:
    global _conn
    if _conn:
        _conn.close()
        _conn = None
