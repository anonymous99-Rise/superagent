from __future__ import annotations

from pathlib import Path

from ..models import Signal
from .base import Collector

TODO_PATTERNS = ("- [ ]", "* [ ]", "TODO", "待办", "待完成", "FIXME")


class ObsidianCollector(Collector):
    name = "obsidian"
    label = "Obsidian 笔记"
    description = "扫描 Obsidian 笔记中的未完成待办与近期笔记"

    def collect(self) -> list[Signal]:
        conf = self.conf()
        vault = Path(conf.get("vault", ""))
        if not vault.exists():
            return []
        out: list[Signal] = []
        for p in vault.rglob("*.md"):
            if ".obsidian" in p.parts:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = p.relative_to(vault)
            for i, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                if any(s.startswith(pat) or (pat in s and len(s) < 160) for pat in TODO_PATTERNS):
                    if s in ("- [x]", "- [X]"):
                        continue
                    out.append(Signal(
                        source=self.name, kind="obsidian_todo",
                        title=f"笔记待办：{s[:80]}", body=f"{rel}:{i}\n{s}",
                        url=str(p), occurred_at=self._mtime_iso(p),
                        project=str(rel.parent) if rel.parent.name else vault.name,
                        importance=0.55, raw={"file": str(p), "line": i},
                    ))
                    if len(out) >= 60:
                        return out
        return out
