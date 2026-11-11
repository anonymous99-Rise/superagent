from __future__ import annotations

import json

from ..models import Signal
from ..util import run_capture, which
from .base import Collector


class GitHubCollector(Collector):
    name = "github"
    label = "GitHub"
    description = "巡检 GitHub 通知、待办 issue、待评审 PR、我发起的 PR 与 CI 失败"

    def available(self) -> tuple[bool, str]:
        if not self.enabled():
            return False, "disabled in config"
        if not which("gh"):
            return False, "gh CLI not installed"
        rc, _, err = run_capture(["gh", "auth", "status"], timeout=20)
        if rc != 0:
            return False, f"gh not authenticated: {err.strip()[:120]}"
        return True, ""

    def collect(self) -> list[Signal]:
        conf = self.conf()
        limit = int(conf.get("limit", 30))
        out: list[Signal] = []
        if conf.get("include_notifications", True):
            out.extend(self._notifications(limit))
        out.extend(self._search_issues(limit))
        out.extend(self._search_prs(limit))
        return out

    def _gh_json(self, argv):
        rc, stdout, err = run_capture(argv, timeout=60)
        if rc != 0:
            return None
        try:
            return json.loads(stdout or "[]")
        except Exception:
            return None

    def _notifications(self, limit: int):
        data = self._gh_json(["gh", "api", f"notifications?per_page={limit}"])
        if not data:
            return []
        out = []
        for n in data:
            if not n.get("unread"):
                continue
            subj = n.get("subject", {}) or {}
            reason = n.get("reason", "")
            title = subj.get("title") or "(no title)"
            repo = (n.get("repository") or {}).get("full_name", "")
            kind = subj.get("type", "")
            imp = 0.75 if reason in ("ci_activity", "review_requested", "assign") else 0.5
            out.append(Signal(
                source=self.name, kind="github_notification",
                title=f"[{repo}] {title}",
                body=f"原因：{reason}\n类型：{kind}\n仓库：{repo}",
                url=(subj.get("latest_comment_url") or subj.get("url") or ""),
                actor=repo, occurred_at=n.get("updated_at", ""), project=repo,
                importance=imp, raw=n,
            ))
        return out

    def _search_issues(self, limit: int):
        data = self._gh_json([
            "gh", "search", "issues", "--assignee", "@me", "--state", "open",
            "--limit", str(limit), "--json", "number,title,url,repository,updatedAt",
        ]) or []
        out = []
        for it in data:
            repo = (it.get("repository") or {}).get("nameWithOwner", "")
            out.append(Signal(
                source=self.name, kind="github_assigned_issue",
                title=f"[{repo}] #{it.get('number')} {it.get('title')}",
                body="分配给我的未关闭 issue。", url=it.get("url", ""),
                project=repo, occurred_at=it.get("updatedAt", ""), importance=0.7, raw=it,
            ))
        return out

    def _search_prs(self, limit: int):
        out = []
        reviewed = self._gh_json([
            "gh", "search", "prs", "--review-requested", "@me", "--state", "open",
            "--limit", str(limit), "--json", "number,title,url,repository,updatedAt",
        ]) or []
        for it in reviewed:
            repo = (it.get("repository") or {}).get("nameWithOwner", "")
            out.append(Signal(
                source=self.name, kind="github_review_request",
                title=f"[{repo}] 待评审 PR #{it.get('number')} {it.get('title')}",
                body="请求我评审的 PR。", url=it.get("url", ""), project=repo,
                occurred_at=it.get("updatedAt", ""), importance=0.7, raw=it,
            ))
        mine = self._gh_json([
            "gh", "search", "prs", "--author", "@me", "--state", "open",
            "--limit", str(limit), "--json", "number,title,url,repository,updatedAt",
        ]) or []
        for it in mine:
            repo = (it.get("repository") or {}).get("nameWithOwner", "")
            out.append(Signal(
                source=self.name, kind="github_my_pr",
                title=f"[{repo}] 我的 PR #{it.get('number')} {it.get('title')}",
                body="我发起且仍打开的 PR。", url=it.get("url", ""), project=repo,
                occurred_at=it.get("updatedAt", ""), importance=0.5, raw=it,
            ))
        return out
