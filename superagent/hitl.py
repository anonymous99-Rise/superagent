from __future__ import annotations

import json

from . import util


class HITL:
    """Human-in-the-loop gate: anything with external/destructive side effects lands in the Outbox."""

    def __init__(self, store, cfg, notifier):
        self.store = store
        self.cfg = cfg
        self.notifier = notifier

    def approval_for_task(self, task_id: str) -> dict | None:
        for a in self.store.approvals(limit=200):
            if a.get("task_id") == task_id:
                return a
        return None

    def has_approved(self, task_id: str) -> bool:
        a = self.approval_for_task(task_id)
        return bool(a and a["status"] == "approved")

    def gate_task(self, task: dict, item: dict | None) -> str:
        """Create a pending approval for a task and park it. Returns approval id."""
        existing = self.approval_for_task(task["id"])
        if existing:
            return existing["id"]
        side = "external"
        title = f"确认执行：{task['title']}"
        summary = (
            f"任务包含对外/敏感动作，需要你确认后才会执行。\n"
            f"任务：{task['title']}\n执行器：{task['agent']}\n"
            f"自主等级：L{task['autonomy_level']}"
        )
        if item:
            summary += f"\n来源事项：{item['title']}"
        aid = self.store.create_approval(
            kind="task_gate", title=title, summary=summary,
            payload={"task_id": task["id"], "agent": task["agent"],
                     "prompt_preview": (task.get("prompt") or "")[:600]},
            risk=side, item_id=task.get("item_id") or "",
            task_id=task["id"],
            options=[{"id": "approve", "label": "批准并执行"},
                     {"id": "reject", "label": "驳回"},
                     {"id": "edit", "label": "先修改任务"}],
        )
        self.store.update_task(task["id"], status="needs_approval")
        self.store.log("warn", "approval.created", title, actor="hitl",
                       entity_type="approval", entity_id=aid,
                       data={"task_id": task["id"]})
        self.notifier.notify_approval(self.store.get_approval(aid) or {})
        return aid

    def approval_for_action(self, action: dict, task: dict, item: dict | None) -> str:
        title = f"确认对外动作：{action.get('title', '未命名动作')}"
        aid = self.store.create_approval(
            kind="external_action", title=title,
            summary=f"Agent 建议执行一个对外动作，已拦截等待你确认。\n任务：{task['title']}",
            payload=action, risk="external",
            item_id=task.get("item_id") or "", task_id=task["id"],
            options=[{"id": "approve", "label": "批准"},
                     {"id": "reject", "label": "驳回"}],
        )
        self.store.log("warn", "approval.created", title, actor="hitl",
                       entity_type="approval", entity_id=aid)
        self.notifier.notify_approval(self.store.get_approval(aid) or {})
        return aid

    def decide(self, approval_id: str, decision: str, note: str = "") -> dict:
        approval = self.store.get_approval(approval_id)
        if not approval:
            raise ValueError("approval not found")
        if decision not in ("approved", "rejected"):
            raise ValueError("decision must be approved/rejected")
        ok = self.store.decide_approval(approval_id, decision, note)
        if not ok:
            raise ValueError("approval already decided")
        task_id = approval.get("task_id")
        if task_id:
            if decision == "approved":
                self.store.update_task(task_id, status="pending", error=None)
                self.store.log("info", "approval.approved", "已批准，任务重新入队", actor="user",
                               entity_type="approval", entity_id=approval_id)
            else:
                self.store.update_task(task_id, status="canceled", finished_at=util.utcnow())
                self.store.log("info", "approval.rejected", "已驳回，任务取消", actor="user",
                               entity_type="approval", entity_id=approval_id)
        return self.store.get_approval(approval_id) or {}

    def outbox(self) -> list[dict]:
        return self.store.approvals(status="pending")
