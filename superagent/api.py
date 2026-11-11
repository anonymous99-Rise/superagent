from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import autonomy, util
from .workers import WORKERS

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if not WEB_DIR.exists():
    WEB_DIR = Path(__file__).resolve().parent / "web"


class App:
    def __init__(self, engine):
        self.engine = engine
        self.cfg = engine.cfg
        self.store = engine.store

    # ---------- views ----------
    def overview(self) -> dict:
        workers = {}
        for name, cls in WORKERS.items():
            w = cls(self.cfg)
            ok, reason = w.available()
            workers[name] = {"available": ok, "reason": reason}
        return {
            "stats": self.store.overview(),
            "patrol": {"last": self.engine.last_patrol, "stats": self.engine.last_patrol_stats,
                       "enabled": bool(self.cfg.get("patrol.enabled", True)),
                       "interval_sec": self.cfg.get("patrol.interval_sec", 900)},
            "workers": workers,
            "collectors": self.store.all_collector_states(),
            "cost_today": self.store.today_cost(),
            "budget": self.cfg.get("policy.daily_cost_budget_usd"),
            "levels": {str(k): {"label": v, "description": autonomy.LEVEL_DESCRIPTIONS[k]}
                       for k, v in autonomy.LEVEL_LABELS.items()},
            "version": "0.1.0",
        }

    def item_detail(self, item_id: str) -> dict:
        item = self.store.get_item(item_id)
        if not item:
            raise KeyError("item not found")
        return {
            "item": item,
            "tasks": self.store.tasks(item_id=item_id),
            "artifacts": self.store.artifacts(item_id=item_id),
            "approvals": [a for a in self.store.approvals(limit=300) if a.get("item_id") == item_id],
            "events": self.store.events(limit=100, entity_id=item_id),
        }

    def digest_view(self, date_str: str = "") -> dict:
        rec = self.store.get_digest(date_str)
        if not rec:
            return {"exists": False, "date": date_str}
        stats = util.json_loads(rec.get("stats_json"), {}) or {}
        content = util.read_text(Path(rec["path"])) if rec.get("path") else ""
        item = self.store.get_item(rec["item_id"]) if rec.get("item_id") else None
        return {"exists": True, "digest": rec, "stats": stats, "markdown": content,
                "item": item}

    def run_logs(self, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if not run:
            raise KeyError("run not found")
        out = util.read_text(Path(run["stdout_path"])) if run.get("stdout_path") else ""
        err = util.read_text(Path(run["stderr_path"])) if run.get("stderr_path") else ""
        return {"run": run, "stdout": out[-200_000:], "stderr": err[-100_000:]}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SuperAgent/0.1"

        def log_message(self, fmt, *args):
            pass

        # ---- helpers ----
        def _json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _err(self, status, message):
            self._json({"ok": False, "error": message}, status)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return {}

        def _authorized(self) -> bool:
            token = app.cfg.get("core.api_token", "")
            if not token:
                return True
            return (self.headers.get("X-Super-Token") == token
                    or parse_qs(urlparse(self.path).query).get("token", [""])[0] == token)

        def _static(self, path: str) -> bool:
            if path in ("/", ""):
                path = "/index.html"
            rel = path.lstrip("/")
            if ".." in rel or rel.startswith("/"):
                return False
            f = (WEB_DIR / rel).resolve()
            if not str(f).startswith(str(WEB_DIR.resolve())) or not f.is_file():
                return False
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
            return True

        # ---- routing ----
        def do_GET(self):
            if not self._authorized():
                return self._err(401, "unauthorized")
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            try:
                if path.startswith("/api/"):
                    return self._api_get(path, q)
                if self._static(path):
                    return
                return self._err(404, "not found")
            except KeyError as e:
                self._err(404, str(e))
            except Exception as e:
                self._err(500, f"{type(e).__name__}: {e}")

        def do_POST(self):
            if not self._authorized():
                return self._err(401, "unauthorized")
            path = urlparse(self.path).path
            try:
                return self._api_post(path, self._body())
            except (KeyError, ValueError) as e:
                self._err(400, str(e))
            except Exception as e:
                self._err(500, f"{type(e).__name__}: {e}")

        def do_PATCH(self):
            if not self._authorized():
                return self._err(401, "unauthorized")
            path = urlparse(self.path).path
            try:
                return self._api_patch(path, self._body())
            except (KeyError, ValueError) as e:
                self._err(400, str(e))
            except Exception as e:
                self._err(500, f"{type(e).__name__}: {e}")

        def do_DELETE(self):
            if not self._authorized():
                return self._err(401, "unauthorized")
            path = urlparse(self.path).path
            try:
                m = re.fullmatch(r"/api/context/([^/]+)", path)
                if m:
                    app.store.delete_context(m.group(1))
                    return self._json({"ok": True})
                return self._err(404, "not found")
            except Exception as e:
                self._err(500, str(e))

        def _api_get(self, path, q):
            s = app.store
            one = lambda k, d=None: (q.get(k, [d])[0] if q.get(k) else d)
            if path == "/api/health":
                return self._json({"ok": True, "data": {"status": "alive", "time": util.utcnow()}})
            if path == "/api/overview":
                return self._json({"ok": True, "data": app.overview()})
            if path == "/api/items":
                return self._json({"ok": True, "data": s.items(status=one("status", "") or "",
                                                              limit=int(one("limit", 200)))})
            m = re.fullmatch(r"/api/items/([^/]+)", path)
            if m:
                return self._json({"ok": True, "data": app.item_detail(m.group(1))})
            if path == "/api/tasks":
                return self._json({"ok": True, "data": s.tasks(item_id=one("item_id", "") or "",
                                                               status=one("status", "") or "",
                                                               limit=int(one("limit", 300)))})
            m = re.fullmatch(r"/api/tasks/([^/]+)", path)
            if m:
                task = s.get_task(m.group(1))
                if not task:
                    raise KeyError("task not found")
                return self._json({"ok": True, "data": {"task": task, "runs": s.runs(task_id=task["id"])}})
            if path == "/api/runs":
                return self._json({"ok": True, "data": s.runs(task_id=one("task_id", "") or "",
                                                              limit=int(one("limit", 100)))})
            m = re.fullmatch(r"/api/runs/([^/]+)/logs", path)
            if m:
                return self._json({"ok": True, "data": app.run_logs(m.group(1))})
            if path == "/api/approvals":
                return self._json({"ok": True, "data": s.approvals(status=one("status", "") or "",
                                                                   limit=int(one("limit", 100)))})
            if path == "/api/artifacts":
                return self._json({"ok": True, "data": s.artifacts(item_id=one("item_id", "") or "",
                                                                  limit=int(one("limit", 100)))})
            if path == "/api/events":
                return self._json({"ok": True, "data": s.events(limit=int(one("limit", 200)),
                                                                level=one("level", "") or "")})
            if path == "/api/context":
                return self._json({"ok": True, "data": s.context()})
            if path == "/api/collectors":
                return self._json({"ok": True, "data": s.all_collector_states()})
            if path == "/api/digest":
                return self._json({"ok": True, "data": app.digest_view(one("date", "") or "")})
            if path == "/api/digests":
                return self._json({"ok": True, "data": s.digests(limit=int(one("limit", 30)))})
            if path == "/api/export":
                return self._json({"ok": True, "data": {
                    "exported_at": util.utcnow(),
                    "items": s.items(limit=10000), "tasks": s.tasks(limit=10000),
                    "approvals": s.approvals(limit=10000), "artifacts": s.artifacts(limit=10000),
                    "context": s.context(), "digests": s.digests(limit=1000),
                }})
            return self._err(404, "unknown endpoint")

        def _api_post(self, path, body):
            e = app.engine
            s = app.store
            if path == "/api/patrol":
                only = body.get("only") or None
                return self._json({"ok": True, "data": e.patrol_once(only=only)})
            if path == "/api/digest/run":
                res = e.digest.build(force=True, notify=bool(body.get("notify", True)))
                res = {k: v for k, v in res.items() if k != "markdown"}
                return self._json({"ok": True, "data": res})
            m = re.fullmatch(r"/api/items/([^/]+)/plan", path)
            if m:
                ids = e.plan_item(m.group(1), use_llm=bool(body.get("use_llm")))
                return self._json({"ok": True, "data": {"task_ids": ids}})
            m = re.fullmatch(r"/api/items/([^/]+)/run", path)
            if m:
                ids = e.run_item(m.group(1), use_llm=bool(body.get("use_llm")))
                return self._json({"ok": True, "data": {"task_ids": ids}})
            m = re.fullmatch(r"/api/items/([^/]+)/archive", path)
            if m:
                e.archive_item(m.group(1))
                return self._json({"ok": True})
            m = re.fullmatch(r"/api/tasks/([^/]+)/retry", path)
            if m:
                return self._json({"ok": e.dispatcher.retry(m.group(1))})
            m = re.fullmatch(r"/api/tasks/([^/]+)/cancel", path)
            if m:
                return self._json({"ok": e.dispatcher.cancel(m.group(1))})
            m = re.fullmatch(r"/api/approvals/([^/]+)/decision", path)
            if m:
                a = e.hitl.decide(m.group(1), body.get("decision", ""), body.get("note", ""))
                return self._json({"ok": True, "data": a})
            if path == "/api/context":
                if not body.get("key") or not body.get("value"):
                    raise ValueError("key and value required")
                s.set_context(body.get("category", "note"), body["key"], body["value"],
                              float(body.get("confidence", 0.8)), "user")
                return self._json({"ok": True})
            return self._err(404, "unknown endpoint")

        def _api_patch(self, path, body):
            m = re.fullmatch(r"/api/items/([^/]+)", path)
            if m:
                allowed = {k: body[k] for k in ("priority", "autonomy_level", "status", "title", "summary")
                           if k in body}
                app.store.update_item(m.group(1), **allowed)
                return self._json({"ok": True, "data": app.store.get_item(m.group(1))})
            return self._err(404, "unknown endpoint")

    return Handler


def serve(engine, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    app = App(engine)
    host = host or engine.cfg.get("core.api_host", "127.0.0.1")
    port = int(port or engine.cfg.get("core.api_port", 8787))
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"SuperAgent dashboard: http://{host}:{port}")
    return httpd
