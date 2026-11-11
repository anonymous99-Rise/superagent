from __future__ import annotations

from collections import Counter, defaultdict

from . import autonomy, util

SOURCE_CAP = {
    "github": 6,
    "filesystem": 4,
    "dsh": 6,
    "zotero": 4,
    "git": 4,
    "obsidian": 4,
    "calendar": 6,
    "email": 6,
}

# file_changed items below this importance are folded into a digest instead of the inbox
FILE_MIN_IMPORTANCE = 0.5


class Triage:
    def __init__(self, store, cfg, context_keywords: list[str] | None = None):
        self.store = store
        self.cfg = cfg
        self.keywords = [k.lower() for k in (context_keywords or []) if k]

    def run(self, signals) -> dict:
        stats = {"seen": len(signals), "created": 0, "duplicate": 0, "skipped": 0, "by_source": Counter()}
        per_source = Counter()
        skipped_by_source = defaultdict(list)
        # highest importance first so caps keep the good stuff
        ordered = sorted(signals, key=lambda s: (s.importance, s.occurred_at), reverse=True)
        for sig in ordered:
            row = sig.to_row()
            is_new = self.store.add_signal(row)
            if not is_new:
                stats["duplicate"] += 1
                continue
            if not self._worth_item(sig):
                stats["skipped"] += 1
                skipped_by_source[sig.source].append(sig)
                self.store.mark_signal(sig.id, "skipped")
                continue
            cap = SOURCE_CAP.get(sig.source, 5)
            if per_source[sig.source] >= cap:
                stats["skipped"] += 1
                skipped_by_source[sig.source].append(sig)
                self.store.mark_signal(sig.id, "deferred")
                continue
            existing = self.store.open_item_with_title(sig.title)
            if existing:
                stats["duplicate"] += 1
                self.store.mark_signal(sig.id, "duplicate", existing["id"])
                continue
            a = autonomy.assess(sig.kind, sig.title, sig.body, sig.importance)
            priority = self._context_boost(a.priority, sig.title + " " + sig.body)
            item_id = self.store.create_item(
                signal_id=sig.id,
                title=sig.title,
                summary=self._summary(sig),
                category=a.category,
                priority=priority,
                autonomy_level=a.level,
                project=sig.project,
                why_now=self._why_now(sig, a),
                recommendation=self._recommend(a),
                status="inbox",
                meta={
                    "source": sig.source, "kind": sig.kind, "url": sig.url,
                    "actor": sig.actor, "occurred_at": sig.occurred_at,
                    "side_effect": a.side_effect, "assessment": a.rationale,
                },
            )
            self.store.mark_signal(sig.id, "item", item_id)
            stats["created"] += 1
            stats["by_source"][sig.source] += 1
            per_source[sig.source] += 1
            self.store.log("info", "item.created", sig.title, actor="triage",
                           entity_type="item", entity_id=item_id,
                           data={"source": sig.source, "level": a.level, "priority": priority})
        self._digests(skipped_by_source, stats)
        return stats

    def _worth_item(self, sig) -> bool:
        if sig.kind == "file_changed" and sig.importance < FILE_MIN_IMPORTANCE:
            return False
        if sig.kind == "git_activity":
            return False
        return True

    def _summary(self, sig) -> str:
        body = (sig.body or "").strip()
        return util.truncate(body, 800) if body else sig.title

    def _why_now(self, sig, a) -> str:
        bits = [f"来自 {sig.source} 的 {sig.kind} 信号"]
        if sig.occurred_at:
            bits.append(f"发生在 {sig.occurred_at}")
        if sig.project:
            bits.append(f"项目/分类：{sig.project}")
        bits.append(a.rationale)
        return "；".join(bits)

    def _recommend(self, a) -> str:
        if a.level <= 1:
            return "可自动归纳要点并归入知识库。"
        if a.level == 2:
            return "可自动分析并给出处理方案，供你选择。"
        if a.level == 3:
            return "可自动产出草稿/中间成果，完成后放入 Outbox 供你审阅。"
        if a.level == 4:
            return "可自主推进大部分工作，关键动作（对外发送/提交/发布）需你确认。"
        return "可端到端执行并自检，仅异常时通知你。"

    def _context_boost(self, priority: int, text: str) -> int:
        low = text.lower()
        for k in self.keywords:
            if k and k in low:
                return max(1, priority - 1)
        return priority

    def _digests(self, skipped_by_source, stats) -> None:
        for source, sigs in skipped_by_source.items():
            if len(sigs) < 10:
                continue
            kinds = Counter(s.kind for s in sigs)
            title = f"{source} 低优先级信号汇总（{len(sigs)} 条）"
            if self.store.open_item_with_title(title):
                continue
            item_id = self.store.create_item(
                title=title,
                summary="本周期内折叠的低优先级变化：" + ", ".join(f"{k}×{v}" for k, v in kinds.items()),
                category="admin", priority=4, autonomy_level=1,
                why_now="为避免打扰，将低重要性信号合并为一条汇总。",
                recommendation="需要时可展开查看，通常无需立即处理。",
                meta={"source": source, "kind": "digest", "side_effect": "local"},
            )
            stats["created"] += 1
            self.store.log("info", "item.digest", title, actor="triage",
                           entity_type="item", entity_id=item_id)
