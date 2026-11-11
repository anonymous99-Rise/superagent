from __future__ import annotations

import os
from pathlib import Path

from ..models import Signal
from ..util import run_capture
from .base import Collector

SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".cache"}


class GitCollector(Collector):
    name = "git"
    label = "本地代码库"
    description = "巡检本地 git 仓库：未提交改动、未推送提交、近期分支活动"

    def collect(self) -> list[Signal]:
        conf = self.conf()
        days = int(conf.get("recency_days", 14))
        max_repos = int(conf.get("max_repos", 50))
        repos = []
        for root in conf.get("roots", []):
            rp = Path(root)
            if not rp.exists():
                continue
            if (rp / ".git").exists():
                repos.append(rp)
                continue
            for dirpath, dirnames, filenames in os.walk(rp):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
                if ".git" in dirnames:
                    repos.append(Path(dirpath))
                    dirnames.remove(".git")
                if len(repos) >= max_repos:
                    break
        out: list[Signal] = []
        for repo in repos[:max_repos]:
            out.extend(self._inspect(repo, days))
        return out

    def _inspect(self, repo: Path, days: int) -> list[Signal]:
        out = []
        rc, status, _ = run_capture(["git", "-C", str(repo), "status", "--porcelain"], timeout=20)
        if rc != 0:
            return out
        dirty = [ln for ln in status.splitlines() if ln.strip()]
        rc2, log, _ = run_capture(
            ["git", "-C", str(repo), "log", f"--since={days} days ago", "--pretty=%h|%an|%ad|%s", "--date=iso"],
            timeout=20,
        )
        commits = [ln for ln in (log or "").splitlines() if ln.strip()]
        name = repo.name
        if dirty:
            out.append(Signal(
                source=self.name, kind="git_dirty", title=f"{name}：{len(dirty)} 处未提交改动",
                body="\n".join(dirty[:30]),
                url=str(repo), occurred_at=self._mtime_iso(repo / ".git" / "HEAD") or None,
                project=name, importance=0.6,
                raw={"repo": str(repo), "dirty": dirty[:50]},
            ))
        if commits:
            latest = commits[0]
            out.append(Signal(
                source=self.name, kind="git_activity", title=f"{name}：近 {days} 天 {len(commits)} 次提交",
                body="\n".join(commits[:10]),
                url=str(repo), occurred_at=self._parse_git_date(latest), project=name,
                importance=0.4, raw={"repo": str(repo), "commits": commits[:20]},
            ))
            rc3, ahead, _ = run_capture(
                ["git", "-C", str(repo), "rev-list", "--count", "@{u}..HEAD"], timeout=20
            )
            if rc3 == 0 and ahead.strip().isdigit() and int(ahead.strip()) > 0:
                out.append(Signal(
                    source=self.name, kind="git_unpushed", title=f"{name}：{ahead.strip()} 个提交未推送",
                    body="存在本地已提交但未推送到远端的提交。",
                    url=str(repo), occurred_at=self._parse_git_date(latest), project=name,
                    importance=0.5, raw={"repo": str(repo), "ahead": int(ahead.strip())},
                ))
        return out

    def _parse_git_date(self, line: str) -> str:
        try:
            from datetime import datetime
            parts = line.split("|")
            dt = datetime.fromisoformat(parts[2].strip())
            from datetime import timezone
            return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except Exception:
            return self._mtime_iso(Path("."))
