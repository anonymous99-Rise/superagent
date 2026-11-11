from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import db, util

ITEM_FIELDS = {
    "title", "summary", "category", "priority", "autonomy_level", "status", "project",
    "why_now", "recommendation", "due_at", "signal_id", "meta_json",
}
TASK_FIELDS = {
    "title", "kind", "agent", "prompt", "status", "depends_on", "autonomy_level",
    "requires_approval", "attempt", "max_attempts", "priority", "timeout_sec",
    "workspace", "started_at", "finished_at", "error", "result_json", "order_idx",
    "next_attempt_at", "parent_id",
}


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        db.init_db(self.db_path)

    @contextmanager
    def _conn(self):
        conn = db.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------------- events ----------------
    def log(self, level: str, action: str, message: str = "", actor: str = "system",
            entity_type: str = "", entity_id: str = "", data=None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO events(ts,level,actor,entity_type,entity_id,action,message,data_json)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (util.utcnow(), level, actor, entity_type, entity_id, action, message,
                 util.json_dumps(data) if data is not None else None),
            )

    def events(self, limit: int = 200, level: str = "", entity_id: str = "") -> list[dict]:
        q = "SELECT * FROM events WHERE 1=1"
        args = []
        if level:
            q += " AND level=?"
            args.append(level)
        if entity_id:
            q += " AND entity_id=?"
            args.append(entity_id)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    # ---------------- signals ----------------
    def add_signal(self, row: dict) -> bool:
        """Insert a signal; returns True when new, False when duplicate."""
        cols = list(row.keys())
        q = f"INSERT OR IGNORE INTO signals({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
        with self._conn() as c:
            cur = c.execute(q, [row[k] for k in cols])
            return cur.rowcount > 0

    def signals(self, source: str = "", status: str = "", limit: int = 100) -> list[dict]:
        q = "SELECT * FROM signals WHERE 1=1"
        args = []
        if source:
            q += " AND source=?"
            args.append(source)
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY collected_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def signal_count(self, since_iso: str = "") -> int:
        with self._conn() as c:
            if since_iso:
                return c.execute("SELECT COUNT(*) FROM signals WHERE collected_at>=?", (since_iso,)).fetchone()[0]
            return c.execute("SELECT COUNT(*) FROM signals").fetchone()[0]

    def mark_signal(self, signal_id: str, status: str, item_id: str = "") -> None:
        with self._conn() as c:
            c.execute("UPDATE signals SET status=?, item_id=? WHERE id=?", (status, item_id or None, signal_id))

    # ---------------- items ----------------
    def create_item(self, *, signal_id: str = "", title: str, summary: str, category: str,
                    priority: int = 3, autonomy_level: int = 1, project: str = "",
                    why_now: str = "", recommendation: str = "", due_at: str = "",
                    status: str = "inbox", meta: dict | None = None) -> str:
        iid = util.new_id("itm")
        now = util.utcnow()
        with self._conn() as c:
            c.execute(
                "INSERT INTO items(id,signal_id,title,summary,category,priority,autonomy_level,"
                "status,project,why_now,recommendation,due_at,created_at,updated_at,meta_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (iid, signal_id or None, util.truncate(title, 300), summary, category, priority,
                 autonomy_level, status, project, why_now, recommendation, due_at, now, now,
                 util.json_dumps(meta or {})),
            )
        return iid

    def item_exists(self, fingerprint_source: str, fingerprint_title: str, category: str) -> bool:
        with self._conn() as c:
            r = c.execute(
                "SELECT 1 FROM items WHERE category=? AND title=? LIMIT 1",
                (category, util.truncate(fingerprint_title, 300)),
            ).fetchone()
            return r is not None

    def open_item_with_title(self, title: str) -> dict | None:
        with self._conn() as c:
            r = c.execute(
                "SELECT * FROM items WHERE title=? AND status IN "
                "('inbox','planned','running','needs_approval') LIMIT 1",
                (util.truncate(title, 300),),
            ).fetchone()
            return dict(r) if r else None

    def get_item(self, item_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return dict(r) if r else None

    def items(self, status: str = "", limit: int = 200, category: str = "") -> list[dict]:
        q = "SELECT * FROM items WHERE 1=1"
        args = []
        if status:
            q += " AND status=?"
            args.append(status)
        if category:
            q += " AND category=?"
            args.append(category)
        q += " ORDER BY priority ASC, created_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def update_item(self, item_id: str, **fields) -> None:
        fields = {k: v for k, v in fields.items() if k in ITEM_FIELDS}
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE items SET {sets}, updated_at=? WHERE id=?",
                      [*fields.values(), util.utcnow(), item_id])

    def delete_item(self, item_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM items WHERE id=?", (item_id,))

    def item_counts(self) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM items GROUP BY status").fetchall()
            return {r["status"]: r["n"] for r in rows}

    # ---------------- tasks ----------------
    def create_task(self, *, item_id: str, title: str, kind: str, agent: str, prompt: str,
                    parent_id: str = "", depends_on: list[str] | None = None,
                    autonomy_level: int = 1, requires_approval: bool = False,
                    priority: int = 3, timeout_sec: int = 900, max_attempts: int = 2,
                    workspace: str = "", order_idx: int = 0, status: str = "pending") -> str:
        tid = util.new_id("tsk")
        with self._conn() as c:
            c.execute(
                "INSERT INTO tasks(id,item_id,parent_id,title,kind,agent,prompt,status,depends_on,"
                "autonomy_level,requires_approval,attempt,max_attempts,priority,timeout_sec,"
                "workspace,created_at,order_idx) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?)",
                (tid, item_id, parent_id or None, title, kind, agent, prompt, status,
                 util.json_dumps(depends_on or []), autonomy_level, int(requires_approval),
                 max_attempts, priority, timeout_sec, workspace, util.utcnow(), order_idx),
            )
        return tid

    def get_task(self, task_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(r) if r else None

    def tasks(self, item_id: str = "", status: str = "", limit: int = 300) -> list[dict]:
        q = "SELECT * FROM tasks WHERE 1=1"
        args = []
        if item_id:
            q += " AND item_id=?"
            args.append(item_id)
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY order_idx ASC, created_at ASC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def update_task(self, task_id: str, **fields) -> None:
        fields = {k: v for k, v in fields.items() if k in TASK_FIELDS}
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE tasks SET {sets} WHERE id=?", [*fields.values(), task_id])

    def claim_task(self, task_id: str) -> bool:
        """Atomically move pending/retrying -> running."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET status='running', started_at=?, attempt=attempt+1 "
                "WHERE id=? AND status IN ('pending','retrying')",
                (util.utcnow(), task_id),
            )
            return cur.rowcount > 0

    def reset_task(self, task_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE tasks SET status='pending', error=NULL WHERE id=?", (task_id,))

    def task_counts(self) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM tasks GROUP BY status").fetchall()
            return {r["status"]: r["n"] for r in rows}

    # ---------------- runs ----------------
    def create_run(self, task_id: str, agent: str, attempt: int) -> str:
        rid = util.new_id("run")
        with self._conn() as c:
            c.execute(
                "INSERT INTO runs(id,task_id,agent,attempt,status,started_at) VALUES(?,?,?,?,?,?)",
                (rid, task_id, agent, attempt, "running", util.utcnow()),
            )
        return rid

    def finish_run(self, run_id: str, *, status: str, exit_code: int | None = None,
                   stdout_path: str = "", stderr_path: str = "", usage: dict | None = None,
                   error: str = "", duration_ms: int = 0) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE runs SET status=?,finished_at=?,duration_ms=?,exit_code=?,stdout_path=?,"
                "stderr_path=?,usage_json=?,error=? WHERE id=?",
                (status, util.utcnow(), duration_ms, exit_code, stdout_path, stderr_path,
                 util.json_dumps(usage or {}), error, run_id),
            )

    def set_run_pid(self, run_id: str, pid: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE runs SET pid=? WHERE id=?", (pid, run_id))

    def today_cost(self) -> float:
        today = util.utcnow()[:10]
        total = 0.0
        with self._conn() as c:
            rows = c.execute(
                "SELECT usage_json FROM runs WHERE started_at LIKE ?", (today + "%",)
            ).fetchall()
        for r in rows:
            try:
                total += float((util.json_loads(r["usage_json"], {}) or {}).get("cost_usd") or 0)
            except Exception:
                pass
        return round(total, 4)

    def runs(self, task_id: str = "", limit: int = 100) -> list[dict]:
        q = "SELECT * FROM runs WHERE 1=1"
        args = []
        if task_id:
            q += " AND task_id=?"
            args.append(task_id)
        q += " ORDER BY started_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(r) if r else None

    # ---------------- approvals ----------------
    def create_approval(self, *, kind: str, title: str, summary: str = "", payload: dict | None = None,
                        risk: str = "external", options: list | None = None,
                        item_id: str = "", task_id: str = "") -> str:
        aid = util.new_id("apr")
        with self._conn() as c:
            c.execute(
                "INSERT INTO approvals(id,item_id,task_id,kind,title,summary,payload_json,risk,"
                "options_json,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (aid, item_id or None, task_id or None, kind, util.truncate(title, 300), summary,
                 util.json_dumps(payload or {}), risk, util.json_dumps(options or []), "pending",
                 util.utcnow()),
            )
        return aid

    def approvals(self, status: str = "", limit: int = 100) -> list[dict]:
        q = "SELECT * FROM approvals WHERE 1=1"
        args = []
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def get_approval(self, approval_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            return dict(r) if r else None

    def decide_approval(self, approval_id: str, decision: str, note: str = "",
                        decided_by: str = "user") -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE approvals SET status=?,decided_at=?,decided_by=?,decision_note=? "
                "WHERE id=? AND status='pending'",
                (decision, util.utcnow(), decided_by, note, approval_id),
            )
            return cur.rowcount > 0

    def pending_approvals_count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0]

    # ---------------- artifacts ----------------
    def add_artifact(self, *, task_id: str = "", item_id: str = "", kind: str, title: str,
                     path: str = "", url: str = "", meta: dict | None = None) -> str:
        aid = util.new_id("art")
        with self._conn() as c:
            c.execute(
                "INSERT INTO artifacts(id,task_id,item_id,kind,title,path,url,created_at,meta_json)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (aid, task_id or None, item_id or None, kind, title, path, url, util.utcnow(),
                 util.json_dumps(meta or {})),
            )
        return aid

    def artifacts(self, item_id: str = "", limit: int = 100) -> list[dict]:
        q = "SELECT * FROM artifacts WHERE 1=1"
        args = []
        if item_id:
            q += " AND item_id=?"
            args.append(item_id)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    # ---------------- digest ----------------
    def digest_seen(self, kind: str, key: str, cooldown_days: float = 0) -> bool:
        with self._conn() as c:
            r = c.execute("SELECT last_seen FROM digest_seen WHERE kind=? AND key=?",
                          (kind, key)).fetchone()
        if not r:
            return False
        if cooldown_days <= 0:
            return True
        return r["last_seen"] >= util.utc_before(cooldown_days)

    def clear_recent_digest_seen(self, days: float = 1) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM digest_seen WHERE last_seen >= ?", (util.utc_before(days),))

    def mark_digest_seen(self, kind: str, key: str) -> None:
        now = util.utcnow()
        with self._conn() as c:
            c.execute(
                "INSERT INTO digest_seen(kind,key,first_seen,last_seen,hits) VALUES(?,?,?,?,1)"
                " ON CONFLICT(kind,key) DO UPDATE SET last_seen=excluded.last_seen,"
                " hits=digest_seen.hits+1",
                (kind, key, now, now),
            )

    def get_digest(self, date: str = "") -> dict | None:
        with self._conn() as c:
            if date:
                r = c.execute("SELECT * FROM digests WHERE date=?", (date,)).fetchone()
            else:
                r = c.execute("SELECT * FROM digests ORDER BY date DESC LIMIT 1").fetchone()
            return dict(r) if r else None

    def digests(self, limit: int = 30) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM digests ORDER BY date DESC LIMIT ?", (limit,))]

    def record_digest(self, date: str, item_id: str, path: str, stats: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO digests(date,item_id,path,stats_json,created_at) VALUES(?,?,?,?,?)"
                " ON CONFLICT(date) DO UPDATE SET item_id=excluded.item_id,path=excluded.path,"
                " stats_json=excluded.stats_json,created_at=excluded.created_at",
                (date, item_id or None, path, util.json_dumps(stats), util.utcnow()),
            )

    # ---------------- context ----------------
    def set_context(self, category: str, key: str, value: str, confidence: float = 0.6,
                    source: str = "user") -> None:
        now = util.utcnow()
        with self._conn() as c:
            existing = c.execute("SELECT id FROM context WHERE key=?", (key,)).fetchone()
            if existing:
                c.execute(
                    "UPDATE context SET category=?,value=?,confidence=?,source=?,updated_at=? WHERE key=?",
                    (category, value, confidence, source, now, key),
                )
            else:
                c.execute(
                    "INSERT INTO context(id,category,key,value,confidence,source,updated_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (util.new_id("ctx"), category, key, value, confidence, source, now),
                )

    def context(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM context ORDER BY category,key")]

    def delete_context(self, key: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM context WHERE key=?", (key,))

    # ---------------- collector state ----------------
    def collector_state(self, name: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM collector_state WHERE name=?", (name,)).fetchone()
            return dict(r) if r else None

    def all_collector_states(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM collector_state ORDER BY name")]

    def set_collector_state(self, name: str, *, ok: bool, stats: dict | None = None,
                            error: str = "") -> None:
        now = util.utcnow()
        with self._conn() as c:
            existing = c.execute("SELECT name FROM collector_state WHERE name=?", (name,)).fetchone()
            if existing:
                c.execute(
                    "UPDATE collector_state SET last_run_at=?, last_ok_at=COALESCE(?,last_ok_at),"
                    " error=?, stats_json=? WHERE name=?",
                    (now, now if ok else None, error, util.json_dumps(stats or {}), name),
                )
            else:
                c.execute(
                    "INSERT INTO collector_state(name,last_run_at,last_ok_at,error,stats_json)"
                    " VALUES(?,?,?,?,?)",
                    (name, now, now if ok else None, error, util.json_dumps(stats or {})),
                )

    # ---------------- overview ----------------
    def overview(self) -> dict:
        with self._conn() as c:
            def one(q, args=()):
                return c.execute(q, args).fetchone()[0]

            today = util.utcnow()[:10]
            return {
                "items": self.item_counts(),
                "tasks": self.task_counts(),
                "pending_approvals": one("SELECT COUNT(*) FROM approvals WHERE status='pending'"),
                "signals_total": one("SELECT COUNT(*) FROM signals"),
                "signals_today": one("SELECT COUNT(*) FROM signals WHERE collected_at LIKE ?", (today + "%",)),
                "runs_total": one("SELECT COUNT(*) FROM runs"),
                "runs_running": one("SELECT COUNT(*) FROM runs WHERE status='running'"),
                "runs_failed": one("SELECT COUNT(*) FROM runs WHERE status='failed'"),
                "success_rate": (lambda ok, tot: round(ok / tot, 3) if tot else None)(
                    one("SELECT COUNT(*) FROM runs WHERE status='succeeded'"),
                    one("SELECT COUNT(*) FROM runs WHERE status IN ('succeeded','failed')"),
                ),
                "artifacts": one("SELECT COUNT(*) FROM artifacts"),
            }
