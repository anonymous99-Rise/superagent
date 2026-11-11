from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from superagent import autonomy
from superagent.config import Config
from superagent.engine import Engine
from superagent.models import Signal
from superagent.planner import Planner
from superagent.store import Store
from superagent.triage import Triage


def make_cfg(tmp: str) -> Config:
    cfg = Config()
    cfg.set("core.data_dir", tmp)
    cfg.set("core.db_path", str(Path(tmp) / "test.db"))
    cfg.set("policy.auto_plan", False)
    cfg.set("notify.desktop", False)
    cfg.set("collectors.filesystem.enabled", False)
    cfg.set("collectors.git.enabled", False)
    cfg.set("collectors.github.enabled", False)
    cfg.set("collectors.zotero.enabled", False)
    cfg.set("collectors.dsh.enabled", False)
    cfg.set("collectors.obsidian.enabled", False)
    cfg.set("collectors.calendar.enabled", False)
    cfg.set("collectors.email.enabled", False)
    return cfg


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(Path(self.tmp) / "t.db")

    def test_signal_dedupe(self):
        s = Signal(source="x", kind="k", title="t")
        self.assertTrue(self.store.add_signal(s.to_row()))
        self.assertFalse(self.store.add_signal(s.to_row()))

    def test_item_and_task_lifecycle(self):
        iid = self.store.create_item(title="a", summary="b", category="admin")
        self.assertEqual(self.store.get_item(iid)["status"], "inbox")
        tid = self.store.create_task(item_id=iid, title="t", kind="noop", agent="local", prompt="p")
        self.assertTrue(self.store.claim_task(tid))
        self.assertFalse(self.store.claim_task(tid))
        self.assertEqual(self.store.get_task(tid)["attempt"], 1)
        self.store.update_task(tid, status="succeeded")
        self.assertEqual(self.store.get_task(tid)["status"], "succeeded")

    def test_approval_decision_idempotent(self):
        aid = self.store.create_approval(kind="task_gate", title="x")
        self.assertTrue(self.store.decide_approval(aid, "approved"))
        self.assertFalse(self.store.decide_approval(aid, "rejected"))

    def test_overview(self):
        self.store.create_item(title="a", summary="b", category="admin")
        o = self.store.overview()
        self.assertEqual(o["items"]["inbox"], 1)


class AutonomyTests(unittest.TestCase):
    def test_levels_and_side_effects(self):
        a = autonomy.assess("zotero_item", "新文献", "", 0.45)
        self.assertEqual(a.category, "reading")
        self.assertEqual(a.level, 1)
        self.assertEqual(a.side_effect, "local")
        b = autonomy.assess("github_review_request", "回复并提交评审", "", 0.8)
        self.assertEqual(b.level, 4)
        self.assertIn(b.side_effect, ("external", "destructive"))
        self.assertEqual(b.priority, 1)

    def test_autorun_policy(self):
        cfg = Config()
        self.assertTrue(autonomy.autorun_allowed(3, "local", cfg))
        self.assertFalse(autonomy.autorun_allowed(5, "external", cfg))
        self.assertTrue(autonomy.requires_approval("external", cfg))


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = make_cfg(self.tmp)
        self.store = Store(Path(self.tmp) / "t.db")

    def test_triage_creates_items_and_dedupes(self):
        sigs = [
            Signal(source="github", kind="github_assigned_issue", title="修复 CI", importance=0.8),
            Signal(source="zotero", kind="zotero_item", title="新文献 A", importance=0.5),
            Signal(source="filesystem", kind="file_changed", title="低价值文件", importance=0.2),
        ]
        t = Triage(self.store, self.cfg)
        stats = t.run(sigs)
        self.assertEqual(stats["created"], 2)
        self.assertEqual(stats["skipped"], 1)
        again = Triage(self.store, self.cfg).run(sigs)
        self.assertEqual(again["created"], 0)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = make_cfg(self.tmp)
        self.store = Store(Path(self.tmp) / "t.db")

    def test_heuristic_plan_materializes_dag(self):
        iid = self.store.create_item(title="写论文初稿", summary="", category="writing",
                                     priority=1, autonomy_level=3)
        planner = Planner(self.store, self.cfg)
        ids = planner.plan_item(iid, use_llm=False)
        self.assertEqual(len(ids), 3)
        tasks = self.store.tasks(item_id=iid)
        self.assertEqual(self.store.get_item(iid)["status"], "planned")
        self.assertEqual(json.loads(tasks[1]["depends_on"]), [ids[0]])
        self.assertEqual(json.loads(tasks[2]["depends_on"]), [ids[1]])
        self.assertTrue(Path(tasks[0]["workspace"]).exists())

    def test_plan_is_idempotent(self):
        iid = self.store.create_item(title="x", summary="", category="admin")
        p = Planner(self.store, self.cfg)
        a = p.plan_item(iid)
        b = p.plan_item(iid)
        self.assertEqual(a, b)


class WorkerArgvTests(unittest.TestCase):
    def test_codex_danger_full_access_bypasses_approval_prompts(self):
        from superagent.workers.codex import CodexWorker

        cfg = Config()
        cfg.set("workers.codex.command", "C:/fake/codex.exe")
        cfg.set("workers.codex.sandbox", "danger-full-access")
        argv = CodexWorker(cfg).build_argv("hi", "C:/ws")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertNotIn("-s", argv)
        self.assertEqual(argv[-1], "hi")

    def test_codex_sandbox_mode_is_passed_when_not_full_access(self):
        from superagent.workers.codex import CodexWorker

        cfg = Config()
        cfg.set("workers.codex.command", "C:/fake/codex.exe")
        cfg.set("workers.codex.sandbox", "workspace-write")
        argv = CodexWorker(cfg).build_argv("hi", "C:/ws")
        self.assertIn("-s", argv)
        self.assertIn("workspace-write", argv)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = make_cfg(self.tmp)
        self.engine = Engine(self.cfg)
        self.store = self.engine.store

    def tearDown(self):
        self.engine.shutdown()

    def _drain(self, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            self.engine.dispatcher.tick()
            counts = self.store.task_counts()
            if not counts.get("pending") and not counts.get("running") and not self.engine.dispatcher.active_count():
                return
            time.sleep(0.2)

    def test_local_task_runs(self):
        iid = self.store.create_item(title="x", summary="", category="admin")
        tid = self.store.create_task(item_id=iid, title="noop", kind="noop", agent="local", prompt="p")
        self._drain()
        self.assertEqual(self.store.get_task(tid)["status"], "succeeded")

    def test_dependency_chain(self):
        iid = self.store.create_item(title="x", summary="", category="admin")
        a = self.store.create_task(item_id=iid, title="a", kind="noop", agent="local", prompt="a")
        b = self.store.create_task(item_id=iid, title="b", kind="noop", agent="local", prompt="b", depends_on=[a])
        self._drain()
        self.assertEqual(self.store.get_task(a)["status"], "succeeded")
        self.assertEqual(self.store.get_task(b)["status"], "succeeded")
        self.assertEqual(self.store.get_item(iid)["status"], "done")

    def test_finalize_registers_artifacts_and_gates_external_action(self):
        from superagent.models import WorkerResult

        iid = self.store.create_item(title="x", summary="", category="communication",
                                     priority=1, autonomy_level=4)
        tid = self.store.create_task(item_id=iid, title="draft", kind="draft",
                                     agent="claude", prompt="p")
        ws = Path(self.tmp) / "ws"
        ws.mkdir()
        (ws / "summary.md").write_text("ok", encoding="utf-8")
        res = WorkerResult(ok=True, summary="done",
                           actions=[{"type": "external_action", "title": "发送邮件",
                                     "payload": {"channel": "email"}}])
        self.engine.dispatcher._finalize_success(
            self.store.get_task(tid), "run_x", res, str(ws), time.time() - 1)

        self.assertEqual(self.store.get_task(tid)["status"], "succeeded")
        arts = self.store.artifacts(item_id=iid)
        self.assertEqual(len(arts), 1, arts)
        self.assertEqual(len(self.store.approvals(status="pending")), 1)
        self.assertEqual(self.store.get_item(iid)["status"], "needs_approval")

    def test_orphan_repair_marks_task_failed_and_refreshes_item(self):
        iid = self.store.create_item(title="x", summary="", category="admin")
        tid = self.store.create_task(item_id=iid, title="t", kind="noop", agent="local", prompt="p")
        self.store.claim_task(tid)
        self.engine.dispatcher.repair_orphans()
        self.assertEqual(self.store.get_task(tid)["status"], "failed")
        self.assertEqual(self.store.get_item(iid)["status"], "failed")

    def test_failure_and_retry(self):
        iid = self.store.create_item(title="x", summary="", category="admin")
        tid = self.store.create_task(item_id=iid, title="verify", kind="verify", agent="local",
                                     prompt="v", max_attempts=1)
        self._drain()
        self.assertEqual(self.store.get_task(tid)["status"], "failed")
        self.assertTrue(self.engine.dispatcher.retry(tid))
        self.assertEqual(self.store.get_task(tid)["status"], "pending")


class ApiTests(unittest.TestCase):
    def test_health_and_items(self):
        import urllib.request
        from superagent.api import serve

        tmp = tempfile.mkdtemp()
        cfg = make_cfg(tmp)
        cfg.set("core.api_port", 0)
        engine = Engine(cfg)
        engine.store.create_item(title="api item", summary="", category="admin")
        httpd = serve(engine, host="127.0.0.1", port=0)
        port = httpd.server_address[1]
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                data = json.loads(r.read().decode())
            self.assertTrue(data["ok"])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/items", timeout=5) as r:
                data = json.loads(r.read().decode())
            self.assertEqual(len(data["data"]), 1)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                html = r.read().decode()
            self.assertIn("SuperAgent", html)
        finally:
            httpd.shutdown()
            engine.shutdown()


if __name__ == "__main__":
    unittest.main()
