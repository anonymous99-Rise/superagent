from __future__ import annotations

from dataclasses import dataclass

LEVEL_LABELS = {
    1: "L1 理解整理",
    2: "L2 分析建议",
    3: "L3 中间交付",
    4: "L4 自主推进(关键确认)",
    5: "L5 端到端交付",
}

LEVEL_DESCRIPTIONS = {
    1: "阅读、归类、提取待办与背景",
    2: "分析问题，给出建议与执行方案",
    3: "完成部分工作，产出可交付中间成果",
    4: "自主完成大部分工作，关键环节需人工确认",
    5: "端到端完成，自主验证并交付",
}

KIND_CATEGORY = {
    "todo": "admin",
    "obsidian_todo": "admin",
    "dsh_todo": "admin",
    "file_changed": "document",
    "zotero_item": "reading",
    "dsh_session": "research",
    "dsh_deliverable": "research",
    "git_dirty": "coding",
    "git_activity": "coding",
    "git_unpushed": "coding",
    "github_notification": "ops",
    "github_assigned_issue": "coding",
    "github_review_request": "review",
    "github_my_pr": "coding",
    "calendar_event": "calendar",
    "email_unread": "communication",
    "digest": "admin",
}

KIND_LEVEL = {
    "file_changed": 1,
    "zotero_item": 1,
    "dsh_session": 1,
    "calendar_event": 1,
    "github_notification": 2,
    "git_activity": 1,
    "git_dirty": 2,
    "git_unpushed": 3,
    "dsh_deliverable": 3,
    "todo": 3,
    "obsidian_todo": 3,
    "dsh_todo": 3,
    "github_assigned_issue": 4,
    "github_review_request": 4,
    "github_my_pr": 4,
    "email_unread": 3,
    "digest": 1,
}

EXTERNAL_WORDS = ["发送", "回复", "提交", "推送", "发布", "邮件", "评论", "联系", "通知他人",
                  "pull request", "reply", "send", "publish", "commit", "push"]
DESTRUCTIVE_WORDS = ["删除", "清空", "覆盖", "重置", "rm -rf", "reset --hard", "drop table"]
URGENT_WORDS = ["截止", "deadline", "逾期", "答辩", "开题", "中期", "投稿", "返修", "失败", "failed"]
LEARNING_WORDS = ["学习", "讲解", "弄懂", "复习", "自测", "练习题", "出题", "掌握路径",
                  "学习路径", "概念梳理", "推导", "study", "learn", "quiz"]


@dataclass
class Assessment:
    category: str
    level: int
    priority: int
    side_effect: str  # local | external | destructive
    rationale: str


def detect_side_effect(text: str) -> str:
    low = (text or "").lower()
    if any(w in text or w in low for w in DESTRUCTIVE_WORDS):
        return "destructive"
    if any(w in text or w in low for w in EXTERNAL_WORDS):
        return "external"
    return "local"


def assess(kind: str, title: str, body: str = "", importance: float = 0.4) -> Assessment:
    text = f"{title}\n{body}"
    category = KIND_CATEGORY.get(kind, "admin")
    low = text.lower()
    if category == "admin" and kind in ("todo", "obsidian_todo", "dsh_todo"):
        if any(w in text or w in low for w in LEARNING_WORDS):
            category = "learning"
    level = KIND_LEVEL.get(kind, 2)
    side = detect_side_effect(text)
    if side != "local":
        level = max(level, 4)
    if category == "review":
        level = max(level, 4)
    if importance >= 0.75:
        priority = 1
    elif importance >= 0.55:
        priority = 2
    elif importance >= 0.4:
        priority = 3
    else:
        priority = 4
    low = text.lower()
    if any(w in text or w in low for w in URGENT_WORDS):
        priority = max(1, priority - 1)
    rationale = (f"kind={kind} → {category}，基础等级 L{level}；"
                 f"副作用={side}；重要度={importance:.2f} → 优先级 P{priority}")
    return Assessment(category=category, level=level, priority=priority,
                      side_effect=side, rationale=rationale)


def requires_approval(side_effect: str, cfg) -> bool:
    if side_effect == "external":
        return bool(cfg.get("policy.external_requires_approval", True))
    if side_effect == "destructive":
        return bool(cfg.get("policy.destructive_requires_approval", True))
    return False


def autorun_allowed(level: int, side_effect: str, cfg) -> bool:
    if side_effect != "local":
        return False
    return level <= int(cfg.get("policy.auto_run_max_level", 4))
