from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..models import Signal
from .base import Collector


def _ms_to_iso(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except Exception:
        return ""


class DshCollector(Collector):
    name = "dsh"
    label = "DSH 会话"
    description = "解析 DSH 会话历史：近期课题、交付物与待办，理解你正在做什么"

    def available(self) -> tuple[bool, str]:
        ok, reason = super().available()
        if not ok:
            return ok, reason
        home = Path(self.conf().get("home", ""))
        if not home.exists():
            return False, f"dsh home not found: {home}"
        try:
            import zstandard  # noqa: F401
        except Exception:
            return False, "python package 'zstandard' not installed (pip install zstandard)"
        return True, ""

    def collect(self) -> list[Signal]:
        conf = self.conf()
        home = Path(conf.get("home"))
        max_sessions = int(conf.get("max_sessions", 40))
        files = glob.glob(str(home / "sessions" / "**" / "*.jsonl.zstd"), recursive=True)
        files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
        out: list[Signal] = []
        for f in files[:max_sessions]:
            out.extend(self._parse(Path(f)))
        return out

    def _parse(self, path: Path) -> list[Signal]:
        import zstandard

        try:
            with open(path, "rb") as fh:
                text = zstandard.ZstdDecompressor().stream_reader(fh).read().decode("utf-8", "replace")
        except Exception:
            return []
        meta = {}
        title = ""
        user_texts: list[str] = []
        deliverables: list[dict] = []
        todos: list[str] = []
        last_time = ""
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            data = o.get("data") or {}
            if o.get("time"):
                last_time = _ms_to_iso(o["time"])
            if t == "session":
                meta = o
            elif t == "session/title":
                title = data.get("title") or title
            elif t == "user/message":
                for part in data.get("content", []) or []:
                    if part.get("type") == "text" and part.get("text"):
                        user_texts.append(part["text"])
            elif t == "deliverables/presented":
                for f in data.get("files", []) or []:
                    deliverables.append(f)
            elif t == "todo/write":
                todos.extend(self._extract_todos(data))
        if not title and user_texts:
            title = user_texts[0][:80]
        if not title:
            title = f"DSH 会话 {path.parent.name[:12]}"
        cwd = meta.get("cwd", "")
        session_id = meta.get("id", path.parent.name)
        out: list[Signal] = []
        recent_user = "\n".join(f"- {u[:300]}" for u in user_texts[-5:])
        out.append(Signal(
            source=self.name, kind="dsh_session", title=f"DSH 会话：{title[:100]}",
            body=f"目录：{cwd}\n最近请求：\n{recent_user}",
            url=f"dsh://session/{session_id}", occurred_at=last_time or _ms_to_iso(meta.get("createdAt")),
            project=Path(cwd).name if cwd else "", importance=0.5,
            raw={"session": session_id, "cwd": cwd, "title": title, "user_texts": user_texts[-8:]},
        ))
        for d in deliverables[-8:]:
            p = d.get("path", "")
            out.append(Signal(
                source=self.name, kind="dsh_deliverable",
                title=f"DSH 交付物：{Path(p).name}",
                body=f"路径：{p}\n说明：{d.get('description', '')}",
                url=p, occurred_at=last_time, project=Path(cwd).name if cwd else "",
                importance=0.7, raw=d,
            ))
        for todo in todos[:10]:
            out.append(Signal(
                source=self.name, kind="dsh_todo", title=f"DSH 待办：{todo[:80]}",
                body=todo, url=f"dsh://session/{session_id}", occurred_at=last_time,
                importance=0.6, raw={"todo": todo},
            ))
        return out

    def _extract_todos(self, data) -> list[str]:
        found: list[str] = []

        def walk(node):
            if isinstance(node, str):
                s = node.strip()
                if s and len(s) < 300 and any(k in s for k in ("[ ]", "TODO", "待办", "pending")):
                    found.append(s)
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(data)
        return found
