from __future__ import annotations

import random
import threading
import time

from . import autonomy, util
from .collectors import ALL
from .context import Context
from .digest import Digest
from .dispatcher import Dispatcher
from .hitl import HITL
from .notify import Notifier
from .planner import Planner
from .store import Store
from .triage import Triage


class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.store = Store(cfg.db_path())
        self.notifier = Notifier(self.store, cfg)
        self.hitl = HITL(self.store, cfg, self.notifier)
        self.context = Context(self.store, cfg)
        self.planner = Planner(self.store, cfg, self.context)
        self.dispatcher = Dispatcher(self.store, cfg, self.hitl, self.notifier)
        self.digest = Digest(self.store, cfg, self.context, self.notifier)
        self._stop = threading.Event()
        self._patrol_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self.last_patrol = ""
        self.last_patrol_stats: dict = {}
        self.store.log("info", "engine.boot", "SuperAgent engine initialized")

    # ---------------- patrol ----------------
    def patrol_once(self, only: list[str] | None = None) -> dict:
        if not self._patrol_lock.acquire(blocking=False):
            return {"skipped": True, "reason": "patrol already running"}
        try:
            return self._patrol_once(only)
        finally:
            self._patrol_lock.release()

    def _patrol_once(self, only: list[str] | None = None) -> dict:
        started = time.time()
        signals = []
        per_source = {}
        for cls in ALL:
            c = cls(self.cfg, self.store)
            if only and c.name not in only:
                continue
            ok, reason = c.available()
            if not ok:
                self.store.set_collector_state(c.name, ok=False, error=reason)
                per_source[c.name] = {"ok": False, "reason": reason}
                continue
            try:
                sigs = c.collect()
                signals.extend(sigs)
                per_source[c.name] = {"ok": True, "signals": len(sigs)}
                self.store.set_collector_state(c.name, ok=True, stats={"signals": len(sigs)})
            except Exception as e:
                self.store.set_collector_state(c.name, ok=False, error=str(e))
                per_source[c.name] = {"ok": False, "reason": str(e)}
                self.store.log("error", "collector.failed", f"{c.name}: {e}", actor="patrol")
        triage = Triage(self.store, self.cfg, self.context.keywords())
        stats = triage.run(signals)
        self.context.refresh(signals)
        planned = self.auto_plan()
        self.last_patrol = util.utcnow()
        self.last_patrol_stats = {
            "signals": len(signals), "created": stats["created"], "duplicate": stats["duplicate"],
            "skipped": stats["skipped"], "planned": planned,
            "by_source": dict(stats["by_source"]), "collectors": per_source,
            "duration_ms": int((time.time() - started) * 1000),
        }
        self.store.log("info", "patrol.done", f"信号 {len(signals)}，新建事项 {stats['created']}，自动规划 {planned}",
                       actor="patrol", data=self.last_patrol_stats)
        return self.last_patrol_stats

    def auto_plan(self) -> int:
        if not bool(self.cfg.get("policy.auto_plan", True)):
            return 0
        if not self.dispatcher.budget_ok():
            return 0
        backlog = int(self.cfg.get("policy.max_pending_backlog", 12))
        if self.store.task_counts().get("pending", 0) >= backlog:
            return 0
        limit = int(self.cfg.get("policy.auto_plan_per_cycle", 2))
        candidates = [
            it for it in self.store.items(status="inbox", limit=50)
            if it["priority"] <= 2 and it["autonomy_level"] >= 2
        ][:limit]
        n = 0
        for it in candidates:
            try:
                self.planner.plan_item(it["id"], use_llm=False)
                n += 1
            except Exception as e:
                self.store.log("error", "plan.failed", str(e), entity_type="item", entity_id=it["id"])
        return n

    # ---------------- item operations ----------------
    def plan_item(self, item_id: str, use_llm: bool = False) -> list[str]:
        return self.planner.plan_item(item_id, use_llm=use_llm)

    def run_item(self, item_id: str, use_llm: bool = False) -> list[str]:
        tasks = self.store.tasks(item_id=item_id)
        if not tasks:
            self.plan_item(item_id, use_llm=use_llm)
        self.store.update_item(item_id, status="running")
        self.dispatcher.tick()
        return [t["id"] for t in self.store.tasks(item_id=item_id)]

    def archive_item(self, item_id: str) -> None:
        self.store.update_item(item_id, status="archived")
        self.store.log("info", "item.archived", "已归档", actor="user",
                       entity_type="item", entity_id=item_id)

    # ---------------- background ----------------
    def start_background(self) -> None:
        if self._threads:
            return
        self.dispatcher.repair_orphans()
        t1 = threading.Thread(target=self._dispatch_loop, name="dispatch-loop", daemon=True)
        t1.start()
        self._threads.append(t1)
        if bool(self.cfg.get("patrol.enabled", True)):
            t2 = threading.Thread(target=self._patrol_loop, name="patrol-loop", daemon=True)
            t2.start()
            self._threads.append(t2)
        if bool(self.cfg.get("digest.enabled", True)):
            t3 = threading.Thread(target=self._digest_loop, name="digest-loop", daemon=True)
            t3.start()
            self._threads.append(t3)
        self.store.log("info", "engine.background_started", "后台巡检、调度与每日推送已启动")

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.dispatcher.tick()
            except Exception as e:
                self.store.log("error", "dispatch.tick_error", str(e))
            self._stop.wait(5)

    def _patrol_loop(self) -> None:
        if bool(self.cfg.get("patrol.run_on_start", True)):
            try:
                self.patrol_once()
            except Exception as e:
                self.store.log("error", "patrol.startup_failed", str(e))
        while not self._stop.is_set():
            interval = int(self.cfg.get("patrol.interval_sec", 900))
            jitter = int(self.cfg.get("patrol.jitter_sec", 30))
            wait = max(60, interval + random.randint(-jitter, jitter))
            if self._stop.wait(wait):
                break
            try:
                self.patrol_once()
            except Exception as e:
                self.store.log("error", "patrol.failed", str(e))

    def _digest_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.digest.due():
                    self.digest.build()
            except Exception as e:
                self.store.log("error", "digest.failed", str(e), actor="digest")
            self._stop.wait(60)

    def shutdown(self) -> None:
        self._stop.set()
        self.dispatcher.shutdown()
        self.store.log("info", "engine.shutdown", "SuperAgent stopping")
