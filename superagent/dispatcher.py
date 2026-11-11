from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import autonomy, util
from .workers import get_worker

TERMINAL_OK = {"succeeded", "skipped"}


class Dispatcher:
    def __init__(self, store, cfg, hitl, notifier=None):
        self.store = store
        self.cfg = cfg
        self.hitl = hitl
        self.notifier = notifier
        self.max_parallel = int(cfg.get("policy.max_parallel_tasks", 3))
        self.pool = ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix="worker")
        self._futures: dict[str, object] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._budget_warned_at = 0.0

    # ---------------- lifecycle ----------------
    def repair_orphans(self) -> None:
        """Recover tasks/runs left running by a previous process. Only the owning daemon calls this."""
        touched = set()
        for t in self.store.tasks(status="running"):
            f = self._futures.get(t["id"])
            if f is None or (hasattr(f, "done") and f.done()):
                self.store.update_task(t["id"], status="failed", finished_at=util.utcnow(),
                                       error="interrupted (process restart)")
                self.store.log("warn", "task.orphan_repaired", t["title"], entity_type="task",
                               entity_id=t["id"])
                if t.get("item_id"):
                    touched.add(t["item_id"])
        for r in self.store.runs(limit=500):
            if r["status"] == "running":
                self.store.finish_run(r["id"], status="interrupted",
                                      error="process restarted while running")
        for item_id in touched:
            self._refresh_item(item_id)

    def shutdown(self) -> None:
        self._stop.set()
        self.pool.shutdown(wait=False, cancel_futures=True)

    # ---------------- scheduling ----------------
    def tick(self) -> dict:
        """One scheduling pass: finalize finished, dispatch ready tasks."""
        self._reap()
        dispatched = 0
        if not self.budget_ok():
            return {"dispatched": 0, "reason": "daily cost budget exhausted"}
        running = len([f for f in self._futures.values() if not f.done()])
        slots = max(0, self.max_parallel - running)
        if slots <= 0:
            return {"dispatched": 0, "reason": "at capacity"}
        for task in self._ready_tasks():
            if slots <= 0:
                break
            if self._stop.is_set():
                break
            if task["requires_approval"] and not self.hitl.has_approved(task["id"]):
                item = self.store.get_item(task["item_id"]) if task["item_id"] else None
                self.hitl.gate_task(task, item)
                self._refresh_item(task["item_id"])
                continue
            if not self.store.claim_task(task["id"]):
                continue
            attempt = int(task.get("attempt") or 0) + 1
            run_id = self.store.create_run(task["id"], task["agent"], attempt)
            fut = self.pool.submit(self._execute, task["id"], run_id, attempt)
            with self._lock:
                self._futures[task["id"]] = fut
            dispatched += 1
            slots -= 1
            self.store.log("info", "task.dispatched", task["title"], entity_type="task",
                           entity_id=task["id"], data={"agent": task["agent"], "attempt": attempt})
        return {"dispatched": dispatched}

    def _ready_tasks(self) -> list[dict]:
        now = util.utcnow()
        out = []
        for t in self.store.tasks(status="pending", limit=200):
            if t.get("next_attempt_at") and t["next_attempt_at"] > now:
                continue
            deps = util.json_loads(t.get("depends_on"), []) or []
            ok = True
            for dep_id in deps:
                dep = self.store.get_task(dep_id)
                if not dep:
                    continue
                if dep["status"] in ("failed", "canceled"):
                    self.store.update_task(t["id"], status="canceled", finished_at=now,
                                           error=f"dependency {dep_id} {dep['status']}")
                    self.store.log("warn", "task.dependency_failed", t["title"],
                                   entity_type="task", entity_id=t["id"])
                    self._refresh_item(t["item_id"])
                    ok = False
                    break
                if dep["status"] not in TERMINAL_OK:
                    ok = False
                    break
            if ok:
                out.append(t)
        out.sort(key=lambda t: (t["priority"], t.get("order_idx") or 0))
        return out

    def _reap(self) -> None:
        with self._lock:
            done = [(tid, f) for tid, f in self._futures.items() if f.done()]
            for tid, f in done:
                self._futures.pop(tid, None)
                try:
                    f.result()
                except Exception as e:  # finalize already handled; just log
                    self.store.log("error", "task.future_error", str(e), entity_type="task",
                                   entity_id=tid)

    def budget_ok(self) -> bool:
        budget = float(self.cfg.get("policy.daily_cost_budget_usd", 0) or 0)
        if budget <= 0:
            return True
        cost = self.store.today_cost()
        if cost < budget:
            return True
        now = time.time()
        if now - self._budget_warned_at > 1800:
            self._budget_warned_at = now
            self.store.log("warn", "budget.exceeded", f"今日成本 ${cost:.4f} ≥ 预算 ${budget:.2f}，暂停派发")
        return False

    # ---------------- execution ----------------
    def _execute(self, task_id: str, run_id: str, attempt: int) -> None:
        task = self.store.get_task(task_id)
        if not task:
            return
        worker_cls = get_worker(task["agent"])
        worker = worker_cls(self.cfg) if worker_cls else None
        if worker is None or not worker.available()[0]:
            fallback = self._fallback_worker(task["agent"])
            if fallback is None:
                self._finalize_failure(task, run_id, attempt, f"no available worker for {task['agent']}")
                return
            worker = fallback
            self.store.log("warn", "worker.fallback", f"{task['agent']} -> {worker.name}",
                           entity_type="task", entity_id=task_id)
        workspace = task.get("workspace") or str(self.cfg.data_path("work", task["item_id"] or "misc"))
        Path(workspace).mkdir(parents=True, exist_ok=True)
        run_dir = self.cfg.data_path("runs", run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        last_msg = str(run_dir / "last_message.txt")
        started = time.time()
        err = "unknown error"
        try:
            res = worker.run(
                task["prompt"], workspace, int(task["timeout_sec"] or 900),
                stdout_path, stderr_path,
                on_pid=lambda pid: self.store.set_run_pid(run_id, pid),
                extra={"last_message_path": last_msg, "task_kind": task["kind"], "local_kind": task["kind"],
                       "paths": self._expected_paths(workspace)},
            )
        except Exception as e:
            res = None
            err = f"{type(e).__name__}: {e}"
        duration = int((time.time() - started) * 1000)
        if res is None:
            self.store.finish_run(run_id, status="failed", error=err, duration_ms=duration)
            self._finalize_failure(task, run_id, attempt, err)
            return
        status = "succeeded" if res.ok else "failed"
        self.store.finish_run(
            run_id, status=status, exit_code=0 if res.ok else 1,
            stdout_path=str(stdout_path), stderr_path=str(stderr_path),
            usage=res.usage, error=res.error, duration_ms=duration,
        )
        if res.ok:
            try:
                self._finalize_success(task, run_id, res, workspace, started)
            except Exception as e:
                self.store.log("error", "task.finalize_error", f"{type(e).__name__}: {e}",
                               entity_type="task", entity_id=task["id"])
                self.store.update_task(task["id"], status="failed", finished_at=util.utcnow(),
                                       error=util.truncate(f"finalize error: {e}", 1000))
                self._refresh_item(task.get("item_id"))
        else:
            self._finalize_failure(task, run_id, attempt, res.error or res.summary or "worker failed")

    def _expected_paths(self, workspace: str) -> list[str]:
        return [str(Path(workspace) / n) for n in ("summary.md", "outline.md", "draft.md",
                                                   "analysis.md", "verify.md", "review.md",
                                                   "diagnosis.md", "reply.md", "brief.md",
                                                   "plan.md", "briefing.md")]

    def _finalize_success(self, task, run_id, res, workspace, started_ts) -> None:
        artifacts = []
        seen = set()
        for a in res.artifacts or []:
            path = a.get("path") or ""
            key = str(Path(path).resolve()).lower() if path else ""
            if key and key not in seen:
                seen.add(key)
                artifacts.append(a)
        if task.get("agent") in ("claude", "codex"):
            try:
                cls = get_worker(task["agent"])
                detected = cls(self.cfg).detect_artifacts(workspace, started_ts) if cls else []
            except Exception as e:
                detected = []
                self.store.log("warn", "artifact.scan_failed", str(e),
                               entity_type="task", entity_id=task["id"])
            for a in detected:
                key = str(Path(a["path"]).resolve()).lower()
                if key not in seen:
                    seen.add(key)
                    artifacts.append(a)
        artifact_ids = []
        for a in artifacts[:30]:
            aid = self.store.add_artifact(
                task_id=task["id"], item_id=task.get("item_id") or "",
                kind=a.get("kind", "file"), title=a.get("title") or Path(a.get("path", "")).name,
                path=a.get("path", ""), meta={"source": "worker"},
            )
            artifact_ids.append(aid)
        # register the run output itself as an artifact when nothing else was produced
        if not artifacts and res.summary:
            self.store.add_artifact(task_id=task["id"], item_id=task.get("item_id") or "",
                                    kind="note", title=task["title"], meta={"summary": res.summary})
        approval_ids = []
        for action in res.actions or []:
            if not isinstance(action, dict):
                continue
            aid = self.hitl.approval_for_action(action, task,
                                                self.store.get_item(task["item_id"]) if task["item_id"] else None)
            approval_ids.append(aid)
        self.store.update_task(
            task["id"], status="succeeded", finished_at=util.utcnow(), error=None,
            result_json=util.json_dumps({"summary": res.summary, "usage": res.usage,
                                         "artifacts": artifact_ids, "approvals": approval_ids}),
        )
        self.store.log("info", "task.succeeded", util.truncate(task["title"], 120),
                       entity_type="task", entity_id=task["id"],
                       data={"artifacts": len(artifacts), "approvals": len(approval_ids),
                             "cost": res.usage.get("cost_usd")})
        # a dependent canceled while this task was failing/unknown may now proceed
        for dep in self.store.tasks(limit=500):
            if dep["status"] == "canceled" and task["id"] in (util.json_loads(dep.get("depends_on"), []) or []):
                self.store.update_task(dep["id"], status="pending", error=None, finished_at=None)
                self.store.log("info", "task.revived", dep["title"], entity_type="task",
                               entity_id=dep["id"])
        self._refresh_item(task.get("item_id"))

    def _finalize_failure(self, task, run_id, attempt, error) -> None:
        max_attempts = int(task.get("max_attempts") or 1)
        if attempt < max_attempts and not self._stop.is_set():
            backoff = min(600, 60 * (2 ** (attempt - 1)))
            next_at = None
            try:
                from datetime import datetime, timedelta, timezone
                next_at = (datetime.now(timezone.utc) + timedelta(seconds=backoff)).isoformat(
                    timespec="seconds").replace("+00:00", "Z")
            except Exception:
                pass
            self.store.update_task(task["id"], status="pending", error=util.truncate(error, 1000),
                                   next_attempt_at=next_at)
            self.store.log("warn", "task.retry", f"{task['title']} 将在 {backoff}s 后重试",
                           entity_type="task", entity_id=task["id"], data={"error": error[:300]})
        else:
            self.store.update_task(task["id"], status="failed", finished_at=util.utcnow(),
                                   error=util.truncate(error, 1000))
            self.store.log("error", "task.failed", util.truncate(task["title"], 120),
                           entity_type="task", entity_id=task["id"], data={"error": error[:500]})
            if self.notifier:
                self.notifier.send("任务失败：" + task["title"][:60],
                                   util.truncate(error, 200), force=True)
        self._refresh_item(task.get("item_id"))

    def _refresh_item(self, item_id: str) -> None:
        if not item_id:
            return
        item = self.store.get_item(item_id)
        if not item:
            return
        tasks = self.store.tasks(item_id=item_id)
        if not tasks:
            return
        statuses = {t["status"] for t in tasks}
        awaiting = [a for a in self.store.approvals(status="pending", limit=200)
                    if a.get("item_id") == item_id]
        if "needs_approval" in statuses or awaiting:
            new = "needs_approval"
        elif "running" in statuses or "pending" in statuses:
            new = "running"
        elif statuses <= {"succeeded", "skipped"}:
            new = "done"
        elif "failed" in statuses:
            new = "failed"
        elif "canceled" in statuses:
            new = "canceled"
        else:
            new = "planned"
        if new != item["status"]:
            self.store.update_item(item_id, status=new)
            if new == "done" and self.notifier:
                self.notifier.send("事项已完成：" + item["title"][:60],
                                   "成果已登记到看板，可查看产物。", force=True)

    def _fallback_worker(self, preferred: str):
        for name in (["codex", "claude"] if preferred == "claude" else ["claude", "codex"]):
            cls = get_worker(name)
            if cls:
                w = cls(self.cfg)
                if w.available()[0]:
                    return w
        return None

    # ---------------- control ----------------
    def cancel(self, task_id: str) -> bool:
        task = self.store.get_task(task_id)
        if not task:
            return False
        if task["status"] == "running":
            for r in self.store.runs(task_id=task_id, limit=5):
                if r["status"] == "running" and r.get("pid"):
                    util.kill_tree(int(r["pid"]))
            self.store.update_task(task_id, status="canceled", finished_at=util.utcnow())
        else:
            self.store.update_task(task_id, status="canceled", finished_at=util.utcnow())
        self.store.log("info", "task.canceled", task["title"], actor="user",
                       entity_type="task", entity_id=task_id)
        self._refresh_item(task.get("item_id"))
        return True

    def retry(self, task_id: str) -> bool:
        task = self.store.get_task(task_id)
        if not task:
            return False
        self.store.update_task(task_id, status="pending", attempt=0, error=None,
                               next_attempt_at=None, finished_at=None)
        self.store.log("info", "task.retried", task["title"], actor="user",
                       entity_type="task", entity_id=task_id)
        self._refresh_item(task.get("item_id"))
        return True

    def active_count(self) -> int:
        with self._lock:
            return len([f for f in self._futures.values() if not f.done()])
