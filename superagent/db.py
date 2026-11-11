from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS signals(
        id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL,
        fingerprint TEXT NOT NULL UNIQUE, title TEXT NOT NULL, body TEXT,
        url TEXT, actor TEXT, occurred_at TEXT, collected_at TEXT NOT NULL,
        raw_json TEXT, project TEXT, importance REAL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'new', item_id TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_signals_source ON signals(source, collected_at)""",
    """CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)""",
    """CREATE TABLE IF NOT EXISTS items(
        id TEXT PRIMARY KEY, signal_id TEXT, title TEXT NOT NULL, summary TEXT,
        category TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 3,
        autonomy_level INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'inbox',
        project TEXT, why_now TEXT, recommendation TEXT, due_at TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, meta_json TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_items_status ON items(status, priority)""",
    """CREATE TABLE IF NOT EXISTS tasks(
        id TEXT PRIMARY KEY, item_id TEXT, parent_id TEXT, title TEXT NOT NULL,
        kind TEXT NOT NULL, agent TEXT NOT NULL, prompt TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', depends_on TEXT,
        autonomy_level INTEGER NOT NULL DEFAULT 1, requires_approval INTEGER NOT NULL DEFAULT 0,
        attempt INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 2,
        priority INTEGER NOT NULL DEFAULT 3, timeout_sec INTEGER NOT NULL DEFAULT 900,
        workspace TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
        error TEXT, result_json TEXT, order_idx INTEGER DEFAULT 0, next_attempt_at TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority)""",
    """CREATE INDEX IF NOT EXISTS idx_tasks_item ON tasks(item_id)""",
    """CREATE TABLE IF NOT EXISTS runs(
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, agent TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'running',
        pid INTEGER, started_at TEXT NOT NULL, finished_at TEXT, duration_ms INTEGER,
        exit_code INTEGER, stdout_path TEXT, stderr_path TEXT, usage_json TEXT, error TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id)""",
    """CREATE TABLE IF NOT EXISTS approvals(
        id TEXT PRIMARY KEY, item_id TEXT, task_id TEXT, kind TEXT NOT NULL,
        title TEXT NOT NULL, summary TEXT, payload_json TEXT,
        risk TEXT NOT NULL DEFAULT 'external', options_json TEXT,
        status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
        decided_at TEXT, decided_by TEXT, decision_note TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status)""",
    """CREATE TABLE IF NOT EXISTS artifacts(
        id TEXT PRIMARY KEY, task_id TEXT, item_id TEXT, kind TEXT NOT NULL,
        title TEXT NOT NULL, path TEXT, url TEXT, created_at TEXT NOT NULL, meta_json TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_artifacts_item ON artifacts(item_id)""",
    """CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, level TEXT NOT NULL,
        actor TEXT, entity_type TEXT, entity_id TEXT, action TEXT NOT NULL,
        message TEXT, data_json TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)""",
    """CREATE TABLE IF NOT EXISTS context(
        id TEXT PRIMARY KEY, category TEXT NOT NULL, key TEXT NOT NULL UNIQUE,
        value TEXT NOT NULL, confidence REAL DEFAULT 0.5, source TEXT, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS collector_state(
        name TEXT PRIMARY KEY, last_run_at TEXT, last_ok_at TEXT, cursor_json TEXT,
        error TEXT, stats_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS digest_seen(
        kind TEXT NOT NULL, key TEXT NOT NULL, first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(kind,key))""",
    """CREATE TABLE IF NOT EXISTS digests(
        date TEXT PRIMARY KEY, item_id TEXT, path TEXT, stats_json TEXT,
        created_at TEXT NOT NULL)""",
]


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        with conn:
            for stmt in SCHEMA:
                conn.execute(stmt)
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] < SCHEMA_VERSION:
                conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
            # lightweight forward migrations
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
            if "next_attempt_at" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN next_attempt_at TEXT")
    finally:
        conn.close()


@contextmanager
def session(path: Path):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
