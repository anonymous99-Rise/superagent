from __future__ import annotations

import re
import threading
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from . import util
from .sources import papers as paper_sources
from .sources import repos as repo_sources
from .sources import skills as skill_sources


class Digest:
    """Daily, zero-LLM-cost research digest: papers, skills, GitHub repos."""

    def __init__(self, store, cfg, context=None, notifier=None):
        self.store = store
        self.cfg = cfg
        self.context = context
        self.notifier = notifier
        self._lock = threading.Lock()

    # ---------------- config helpers ----------------
    def keywords(self) -> list[str]:
        words = [str(k).strip() for k in (self.cfg.get("digest.keywords") or []) if str(k).strip()]
        if self.context is not None:
            for k in self.context.keywords():
                k = (k or "").strip()
                if len(k) >= 3 and any(ch.isascii() and ch.isalpha() for ch in k):
                    words.append(k)
        out = []
        for w in words:
            if w.lower() not in (x.lower() for x in out):
                out.append(w)
        return out[:16]

    def _time_due(self, now: datetime) -> bool:
        target = str(self.cfg.get("digest.time", "08:00"))
        try:
            hour, minute = (int(x) for x in target.split(":", 1))
        except (ValueError, TypeError):
            return False
        return (now.hour, now.minute) >= (hour, minute)

    def due(self, now: datetime | None = None) -> bool:
        if not bool(self.cfg.get("digest.enabled", True)):
            return False
        today = (now or datetime.now()).date().isoformat()
        if self.store.get_digest(today):
            return False
        return self._time_due(now or datetime.now())

    # ---------------- main ----------------
    def build(self, force: bool = False, notify: bool = True) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"skipped": True, "reason": "digest already running"}
        try:
            return self._build(force=force, notify=notify)
        finally:
            self._lock.release()

    def _build(self, force: bool, notify: bool) -> dict:
        today = date.today().isoformat()
        existing = self.store.get_digest(today)
        if not force and existing:
            return {"skipped": True, "reason": "already generated today", "date": today}
        if force and existing:
            # Manual rebuild: make today's recommendations eligible again.
            self.store.clear_recent_digest_seen(days=1)

        keywords = self.keywords()
        sources = list(self.cfg.get("digest.sources") or ["pubmed", "europepmc", "arxiv"])
        days = int(self.cfg.get("digest.lookback_days", 7))
        max_papers = int(self.cfg.get("digest.max_papers", 8))
        per_source = int(self.cfg.get("digest.max_per_source", 4))
        max_skills = int(self.cfg.get("digest.max_skills", 4))
        max_github_skills = int(self.cfg.get("digest.max_github_skills", 2))
        max_repos = int(self.cfg.get("digest.max_repos", 5))
        errors: dict = {}

        all_papers, paper_errors, source_counts = paper_sources.collect(
            keywords, sources, days=days, limit=int(self.cfg.get("digest.source_limit", 40)))
        errors.update({f"paper:{k}": v for k, v in paper_errors.items()})

        min_score = float(self.cfg.get("digest.min_paper_score", 3.0))
        fresh = [p for p in all_papers
                 if p["score"] >= min_score and not self.store.digest_seen("paper", p["key"])]
        picked = self._pick_papers(fresh, max_papers, per_source)

        local_skills, skill_errors = skill_sources.collect(
            list(self.cfg.get("digest.skill_dirs") or []), keywords,
            limit=max_skills * 8)
        errors.update(skill_errors)
        cooldown = float(self.cfg.get("digest.skill_cooldown_days", 14))
        local_skills = [s for s in local_skills
                        if not self.store.digest_seen("skill", s["key"], cooldown_days=cooldown)]
        picked_skills = self._pick_skills(local_skills, max_skills)

        skill_words = keywords + list(self.cfg.get("digest.github_skills_keywords") or [])
        gh_skills, gh_skill_errors = repo_sources.collect(
            list(self.cfg.get("digest.github_skills_queries") or []), skill_words,
            min_stars=int(self.cfg.get("digest.github_skills_min_stars", 20)),
            max_age_days=int(self.cfg.get("digest.github_skills_max_age_days", 730)),
            per_page=8, exclude=list(self.cfg.get("digest.exclude_users") or []))
        errors.update({f"skills:{k}": v for k, v in gh_skill_errors.items()})
        picked_gh_skills = [r for r in gh_skills
                            if r["matched"] and not self.store.digest_seen("repo", r["key"])][:max_github_skills]

        all_repos, repo_errors = repo_sources.collect(
            list(self.cfg.get("digest.repo_queries") or []), keywords,
            min_stars=int(self.cfg.get("digest.min_repo_stars", 25)),
            max_age_days=int(self.cfg.get("digest.repo_max_age_days", 365)),
            per_page=int(self.cfg.get("digest.repos_per_query", 8)),
            exclude=list(self.cfg.get("digest.exclude_users") or []))
        errors.update({f"repo:{k}": v for k, v in repo_errors.items()})
        skill_repo_keys = {r["key"] for r in gh_skills}
        blocked = [str(t).lower() for t in (self.cfg.get("digest.blocked_repo_terms") or [])]
        picked_gh_skills = [r for r in picked_gh_skills if not self._blocked(r, blocked)]
        picked_repos = [r for r in all_repos
                        if r["matched"] and r["key"] not in skill_repo_keys
                        and not self._blocked(r, blocked)
                        and not self.store.digest_seen("repo", r["key"])][:max_repos]

        counts = {
            "papers": len(picked),
            "skills": len(picked_skills) + len(picked_gh_skills),
            "local_skills": len(picked_skills),
            "github_skills": len(picked_gh_skills),
            "repos": len(picked_repos),
        }
        stats = {
            "date": today,
            "generated_at": util.utcnow(),
            "keywords": keywords,
            "sources": sources,
            "counts": counts,
            "papers": [self._paper_stat(p) for p in picked],
            "skills": [self._skill_stat(s) for s in picked_skills]
                      + [self._repo_stat(r) for r in picked_gh_skills],
            "repos": [self._repo_stat(r) for r in picked_repos],
            "source_counts": source_counts,
            "errors": errors,
            "cost_usd": 0.0,
        }

        markdown = self._render(today, stats)
        path = self.cfg.data_path("digests", f"{today}.md")
        util.atomic_write(path, markdown)
        obsidian_path = ""
        if bool(self.cfg.get("digest.obsidian_sync", True)):
            obsidian_path = self._sync_obsidian(today, markdown, stats)
            if obsidian_path:
                stats["obsidian_path"] = obsidian_path

        item_id = ""
        if counts["papers"] + counts["skills"] + counts["repos"] > 0:
            item_id = self._upsert_item(today, stats, path)
            stats["item_id"] = item_id
        else:
            self.store.log("warn", "digest.empty",
                           "今日没有新的匹配内容" + (f"；数据源错误 {len(errors)} 个" if errors else ""))

        self.store.record_digest(today, item_id, str(path), stats)
        for p in picked:
            self.store.mark_digest_seen("paper", p["key"])
        for s in picked_skills:
            self.store.mark_digest_seen("skill", s["key"])
        for r in picked_gh_skills + picked_repos:
            self.store.mark_digest_seen("repo", r["key"])

        self.store.log("info", "digest.created",
                       f"论文 {counts['papers']}，技能 {counts['skills']}，仓库 {counts['repos']}",
                       actor="digest", data={"date": today, "path": str(path),
                                             "errors": sorted(errors), **counts})
        if notify and item_id and bool(self.cfg.get("digest.notify", True)) and self.notifier:
            title = f"每日科研推送 · {counts['papers']} 篇论文"
            body = self._toast_body(stats)
            self.notifier.send(title, body, force=True, key="digest:" + today)

        return {"ok": True, "date": today, "item_id": item_id, "path": str(path),
                "obsidian_path": obsidian_path, "counts": counts, "errors": errors,
                "markdown": markdown}

    # ---------------- selection ----------------
    @staticmethod
    def _pick_skills(candidates: list[dict], limit: int) -> list[dict]:
        """Round-robin across skill families so every day covers a different area."""
        groups: dict[str, list[dict]] = {}
        for s in candidates:
            family = (s.get("name") or "").split("-")[0].lower() or "misc"
            groups.setdefault(family, []).append(s)
        picked: list[dict] = []
        while len(picked) < limit and any(groups.values()):
            for family in list(groups):
                if groups[family] and len(picked) < limit:
                    picked.append(groups[family].pop(0))
                elif not groups[family]:
                    del groups[family]
        return picked

    @staticmethod
    def _pick_papers(candidates: list[dict], limit: int, per_source: int) -> list[dict]:
        picked: list[dict] = []
        keys = set()
        by_source: Counter = Counter()
        for p in candidates:
            if len(picked) >= limit:
                break
            if by_source[p["source"]] >= per_source:
                continue
            picked.append(p)
            keys.add(p["key"])
            by_source[p["source"]] += 1
        if len(picked) < limit:
            for p in candidates:
                if p["key"] not in keys:
                    picked.append(p)
                    keys.add(p["key"])
                    if len(picked) >= limit:
                        break
        return picked

    # ---------------- rendering ----------------
    @staticmethod
    def _blocked(repo: dict, terms: list[str]) -> bool:
        hay = (repo.get("name", "") + " " + repo.get("description", "")).lower()
        return any(t and t in hay for t in terms)

    @staticmethod
    def _paper_stat(p: dict) -> dict:
        stat = {k: p.get(k, "") for k in
                ("title", "journal", "published", "source", "doi", "url", "score", "matched")}
        stat["abstract"] = util.truncate(p.get("abstract", ""), 700)
        return stat

    @staticmethod
    def _skill_stat(s: dict) -> dict:
        return {k: s.get(k, "") for k in
                ("name", "description", "path", "score", "matched", "source")}

    @staticmethod
    def _repo_stat(r: dict) -> dict:
        return {k: r.get(k, "") for k in
                ("name", "url", "description", "stars", "language", "pushed", "topics",
                 "score", "matched")}

    def _render(self, today: str, stats: dict) -> str:
        c = stats["counts"]
        lines = [
            f"# 每日科研推送 · {today}",
            "",
            f"> 论文 {c['papers']} · 科研 Skills {c['skills']} · GitHub 仓库 {c['repos']}",
            f"> 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}（本地）"
            f" · 数据源 {' / '.join(stats['sources'])}",
            f"> 关注词：{'、'.join(stats['keywords'][:10])}",
            "",
            "## 论文",
            "",
        ]
        if stats["papers"]:
            for i, p in enumerate(stats["papers"], 1):
                meta = [p.get("journal") or p.get("source", ""), p.get("published") or ""]
                meta = [m for m in meta if m]
                lines.append(f"### {i}. {p['title']}")
                if meta:
                    lines.append("`" + "` · `".join(meta) + "`")
                if p.get("matched"):
                    lines.append(f"匹配：{'、'.join(p['matched'][:6])}")
                if p.get("abstract"):
                    lines.append("")
                    lines.append("> " + p["abstract"].replace("\n", " ")[:600])
                links = []
                if p.get("url"):
                    links.append(f"[原文]({p['url']})")
                if p.get("doi"):
                    links.append(f"[DOI](https://doi.org/{p['doi']})")
                if links:
                    lines.append("")
                    lines.append(" · ".join(links))
                lines.append("")
        else:
            lines += ["_今日没有新的匹配论文。_", ""]

        lines += ["## 科研 Skills", ""]
        local = [s for s in stats["skills"] if s.get("source") == "local"]
        remote = [s for s in stats["skills"] if s.get("source") != "local"]
        if local:
            lines += ["### 本地 DSH / Claude Skills", ""]
            for s in local:
                desc = f" — {s['description']}" if s.get("description") else ""
                lines.append(f"- **{s['name']}**{desc}")
                if s.get("path"):
                    lines.append(f"  `{s['path']}`")
                if s.get("matched"):
                    lines.append(f"  匹配：{'、'.join(s['matched'][:6])}")
            lines.append("")
        if remote:
            lines += ["### GitHub 技能仓库", ""]
            for r in remote:
                lines.append(self._repo_line(r))
            lines.append("")
        if not local and not remote:
            lines += ["_没有匹配的新技能。_", ""]

        lines += ["## GitHub 仓库", ""]
        if stats["repos"]:
            for r in stats["repos"]:
                lines.append(self._repo_line(r))
            lines.append("")
        else:
            lines += ["_今日没有新的推荐仓库。_", ""]

        lines += ["## 采集情况", ""]
        for name, n in sorted(stats.get("source_counts", {}).items()):
            lines.append(f"- {name}：{n} 条候选")
        if stats.get("errors"):
            lines.append("")
            lines.append("**数据源异常**")
            for name, err in sorted(stats["errors"].items()):
                lines.append(f"- {name}：{util.truncate(str(err), 300)}")
        lines += ["", "---", "",
                  "本推送由 SuperAgent 本地生成，不调用大模型、不产生 API 成本。",
                  "感兴趣的条目可在看板 Inbox 中转为精读或调研任务。", ""]
        return "\n".join(lines)

    @staticmethod
    def _repo_line(r: dict) -> str:
        stars = r.get("stars") or 0
        star = f"★{stars/1000:.1f}k" if stars >= 1000 else f"★{stars}"
        bits = [star]
        if r.get("language"):
            bits.append(str(r["language"]))
        if r.get("pushed"):
            bits.append("更新 " + str(r["pushed"]))
        if r.get("matched"):
            bits.append("相关：" + "、".join(r["matched"][:4]))
        desc = f" — {r['description']}" if r.get("description") else ""
        return f"- **[{r['name']}]({r['url']})** {' · '.join(bits)}{desc}"

    def _toast_body(self, stats: dict) -> str:
        titles = [p["title"] for p in stats.get("papers", [])[:2]]
        body = "；".join(titles) if titles else "今日有新的技能与仓库推荐"
        return body[:280]

    # ---------------- obsidian sync ----------------
    def _sync_obsidian(self, today: str, markdown: str, stats: dict) -> str:
        raw = str(self.cfg.get("digest.obsidian_vault") or "").strip()
        if not raw:
            return ""
        vault = Path(raw).expanduser()
        try:
            folder = vault / "每日推送"
            folder.mkdir(parents=True, exist_ok=True)
            body = markdown
            first, _, rest = body.partition("\n")
            if first.startswith("# "):
                body = rest.lstrip("\n")
            c = stats.get("counts", {})
            front = [
                "---",
                f'title: "每日科研推送 · {today}"',
                f"date: {today}",
                "type: digest",
                "tags: [SuperAgent, 每日推送, 科研]",
                f"papers: {int(c.get('papers', 0))}",
                f"skills: {int(c.get('skills', 0))}",
                f"repos: {int(c.get('repos', 0))}",
                f"updated: {today}",
                "---",
                "",
            ]
            note = "\n".join(front) + body.rstrip() + "\n\n---\n\n返回 [[首页]]\n"
            note_path = folder / f"{today}.md"
            util.atomic_write(note_path, note)
            self._write_obsidian_index(vault, today, c)
            return str(note_path)
        except OSError as e:
            self.store.log("warn", "digest.obsidian_failed", f"同步 Obsidian 失败：{e}")
            return ""

    def _write_obsidian_index(self, vault: Path, today: str, counts: dict) -> None:
        folder = vault / "每日推送"
        rows = []
        for f in sorted(folder.glob("*.md"), key=lambda p: p.stem, reverse=True)[:60]:
            day = f.stem
            c = counts if day == today else self._note_counts(f)
            rows.append(
                f"- [[每日推送/{day}|{day}]] — 论文 {c.get('papers', 0)} · "
                f"技能 {c.get('skills', 0)} · 仓库 {c.get('repos', 0)}")
        if not rows:
            rows = ["_还没有推送。_"]
        lines = [
            "---",
            "title: SuperAgent 建议",
            "type: index",
            "tags: [SuperAgent, 每日推送]",
            f"updated: {today}",
            "---",
            "",
            "# SuperAgent 建议",
            "",
            "每天定时，SuperAgent 会把和你研究方向相关的**论文、科研 Skills、"
            "GitHub 仓库**整理到 `每日推送/` 文件夹。",
            "内容全部来自本地脚本与公开数据源，不调用大模型、不产生 API 费用。",
            "",
            "## 最新推送",
            "",
            *rows,
            "",
            "## 怎么用",
            "",
            "- 点上面的日期打开当天推送，论文标题可直接点开原文或 DOI。",
            "- 看到想读的论文，在 SuperAgent 看板的 Inbox 里转成精读任务。",
            "- 历史推送都会保留，方便回看。",
            "",
            "## 看板",
            "",
            "- [打开 SuperAgent 看板](http://127.0.0.1:8787)",
            "- 看板里的「每日推送」页面可以回看历史或手动重新生成。",
            "",
            "> 本文件由 SuperAgent 自动维护，手动修改会在下次推送时被覆盖。",
            "",
        ]
        util.atomic_write(vault / "首页.md", "\n".join(lines))

    @staticmethod
    def _note_counts(path: Path) -> dict:
        head = util.read_text(path, 1500)
        out = {}
        for key in ("papers", "skills", "repos"):
            m = re.search(rf"^{key}:\s*(\d+)", head, re.MULTILINE)
            out[key] = int(m.group(1)) if m else 0
        return out

    # ---------------- inbox item ----------------
    def _upsert_item(self, today: str, stats: dict, path: Path) -> str:
        c = stats["counts"]
        top = stats["papers"][0]["title"] if stats.get("papers") else ""
        summary = (f"论文 {c['papers']} 篇、科研 Skills {c['skills']} 个、"
                   f"GitHub 仓库 {c['repos']} 个。")
        if top:
            summary += f" 头条：{top}"
        title = f"每日科研推送 · {today}"
        meta = {"source": "digest", "kind": "daily_digest", "date": today,
                "path": str(path), "counts": c}

        existing = self.store.get_digest(today)
        item = self.store.get_item(existing["item_id"]) if existing and existing.get("item_id") else None
        if item is None:
            item = self.store.open_item_with_title(title)
        if item:
            item_id = item["id"]
            self.store.update_item(
                item_id, title=title, summary=summary, category="digest", priority=2,
                autonomy_level=1, project="文献追踪", status="inbox",
                why_now=f"每日定时推送（{self.cfg.get('digest.time', '08:00')}）",
                recommendation="先浏览论文；感兴趣的条目可转为精读/调研任务，技能与仓库可按需试用。",
                meta_json=util.json_dumps(meta))
        else:
            item_id = self.store.create_item(
                title=title, summary=summary, category="digest", priority=2,
                autonomy_level=1, project="文献追踪", status="inbox",
                why_now=f"每日定时推送（{self.cfg.get('digest.time', '08:00')}）",
                recommendation="先浏览论文；感兴趣的条目可转为精读/调研任务，技能与仓库可按需试用。",
                meta=meta)
            self._archive_old_digests(today)

        exists = any((a.get("path") or "") == str(path)
                     for a in self.store.artifacts(item_id=item_id, limit=100))
        if not exists:
            self.store.add_artifact(item_id=item_id, kind="digest", title=title,
                                    path=str(path), meta={"date": today, **c})
        return item_id

    def _archive_old_digests(self, today: str) -> None:
        for item in self.store.items(limit=500):
            if item.get("category") != "digest" or item.get("status") == "archived":
                continue
            meta = util.json_loads(item.get("meta_json"), {}) or {}
            if meta.get("date") and meta.get("date") != today:
                self.store.update_item(item["id"], status="archived")
