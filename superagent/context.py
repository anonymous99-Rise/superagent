from __future__ import annotations

from pathlib import Path

from . import util

SEED = [
    ("profile", "role", "科研/学术工作者（植物学、微生物方向）", 0.9, "inferred"),
    ("profile", "identity_note", "正在推进毕业论文与植物/光合作用相关研究", 0.8, "inferred"),
    ("project", "thesis", "毕业论文：植物学/微生物相关，含任务下达书与论文文档", 0.8, "filesystem"),
    ("project", "plant_systematics", "植物系统学：PGA2 质体注释、MAFFT、RAxML、IQ-TREE、BLAST 流程", 0.8, "filesystem"),
    ("project", "microbiology", "微生物：图像处理、数据与文献整理（Zotero 分类“微生物”）", 0.8, "zotero"),
    ("topic", "photosynthesis", "光合作用：PSI/PSII、蓝藻、叶绿体、光保护与组装因子", 0.8, "zotero"),
    ("topic", "phylogenetics", "系统发育与质体基因组注释", 0.7, "filesystem"),
    ("preference", "notification", "通过 Windows 桌面通知推送需要决策的事项", 0.9, "user"),
    ("preference", "trust_boundary", "本地读写与分析可自主执行；对外发送/提交/发布必须进入 Outbox 确认", 0.95, "user"),
    ("goal", "long_term", "完成毕业论文，并推进相关研究写作与投稿", 0.7, "inferred"),
    ("tool", "stack", "Claude Code、Codex、DSH（学术）、Zotero、Obsidian、Geneious/SnapGene", 0.85, "inferred"),
]


class Context:
    def __init__(self, store, cfg):
        self.store = store
        self.cfg = cfg
        self._seed()

    def _seed(self) -> None:
        if self.store.context():
            return
        for category, key, value, confidence, source in SEED:
            self.store.set_context(category, key, value, confidence, source)

    def refresh(self, signals=None) -> dict:
        """Derive a few facts from recent signals without clobbering user edits."""
        if not signals:
            return {}
        from collections import Counter

        topics = Counter()
        projects = Counter()
        for s in signals:
            if s.source == "zotero" and s.kind == "zotero_item":
                for w in ("光合", "PSI", "PSII", "蓝藻", "叶绿体", "质体", "系统发育", "微生物"):
                    if w.lower() in (s.title + " " + s.body).lower():
                        topics[w] += 1
            if s.project:
                projects[s.project] += 1
        if topics:
            top = "、".join(k for k, _ in topics.most_common(6))
            self.store.set_context("topic", "active_topics", f"近期文献关注：{top}", 0.6, "zotero")
        if projects:
            top = "、".join(k for k, _ in projects.most_common(6))
            self.store.set_context("project", "active_projects", f"近期活跃项目/目录：{top}", 0.5, "collectors")
        return {}

    def text(self, limit: int = 40) -> str:
        rows = self.store.context()[:limit]
        if not rows:
            return ""
        lines = []
        for r in rows:
            lines.append(f"- [{r['category']}] {r['key']}: {r['value']}")
        return "\n".join(lines)

    def keywords(self) -> list[str]:
        kws = []
        for r in self.store.context():
            if r["category"] in ("project", "topic"):
                for part in r["value"].replace("：", ":").split(":"):
                    for piece in part.replace("、", ",").replace("/", ",").split(","):
                        p = piece.strip()
                        if 2 <= len(p) <= 12:
                            kws.append(p)
        return list(dict.fromkeys(kws))[:30]

    def profile_markdown(self) -> str:
        path = self.cfg.root("config", "profile.md")
        return util.read_text(path) if path.exists() else ""
