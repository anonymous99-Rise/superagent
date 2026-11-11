from __future__ import annotations

import os
import re
from pathlib import Path

from ..models import Signal
from .base import Collector

SKIP_DIRS = {
    "node_modules", ".git", ".svn", "__pycache__", ".venv", "venv", "site-packages",
    "AppData", "Library", ".cache", ".npm", ".cargo", ".rustup", ".conda", "pkgs",
    ".idea", ".vscode", "dist", "build", ".next", "target", "vendor", "attachments",
}
TODO_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?(?:TODO|FIXME|待办|待完成|需要|记得|DONE\?|\[ \])[：:\s].{0,200}$")
IMPORTANT_WORDS = ["毕业论文", "开题", "中期", "答辩", "截止", "deadline", "投稿", "返修", "基金", "申请", "汇报", "组会"]
TEXT_EXT = {".md", ".txt", ".tex", ".csv", ".py", ".json", ".toml", ".yaml", ".yml", ".r", ".ipynb", ".html", ".js", ".ts"}
PLACEHOLDER_PREFIXES = ("~$", ".~", "~WRL")


class FilesystemCollector(Collector):
    name = "filesystem"
    label = "本地文档"
    description = "扫描桌面与文档目录，发现近期改动文件、待办标记与重要文档"

    def collect(self) -> list[Signal]:
        conf = self.conf()
        days = int(conf.get("recency_days", 14))
        max_files = int(conf.get("max_files", 500))
        exts = {e.lower() for e in conf.get("extensions", [])}
        out: list[Signal] = []
        seen = 0
        for base in conf.get("paths", []):
            root = Path(base)
            if not root.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
                if seen >= max_files:
                    break
                for fn in filenames:
                    if fn.startswith(PLACEHOLDER_PREFIXES) or fn.lower() == "desktop.ini":
                        continue
                    p = Path(dirpath) / fn
                    ext = p.suffix.lower()
                    if exts and ext not in exts:
                        continue
                    if not self._recent(p, days):
                        continue
                    seen += 1
                    try:
                        size = p.stat().st_size
                    except OSError:
                        continue
                    importance = self._importance(str(p), fn)
                    out.append(Signal(
                        source=self.name, kind="file_changed", title=f"近期改动：{fn}",
                        body=f"路径：{p}\n大小：{size} 字节\n修改时间：{self._mtime_iso(p)}",
                        url=str(p), occurred_at=self._mtime_iso(p),
                        project=self._project_of(root, p), importance=importance,
                        raw={"path": str(p), "size": size},
                    ))
                    if ext in TEXT_EXT and size < 400_000:
                        out.extend(self._todos(p, fn))
                    if seen >= max_files:
                        break
        return out

    def _todos(self, path: Path, name: str) -> list[Signal]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        out = []
        for m in TODO_RE.finditer(text):
            line = m.group(0).strip()
            if len(line) < 4:
                continue
            out.append(Signal(
                source=self.name, kind="todo", title=f"待办：{line[:80]}",
                body=f"文件：{path}\n内容：{line}",
                url=str(path), occurred_at=self._mtime_iso(path),
                importance=0.55, raw={"path": str(path), "line": line},
            ))
            if len(out) >= 10:
                break
        return out

    def _importance(self, path: str, name: str) -> float:
        score = 0.25
        low = path.lower()
        for w in IMPORTANT_WORDS:
            if w in low:
                score += 0.25
                break
        if name.lower().endswith((".docx", ".pdf", ".pptx")):
            score += 0.1
        return min(score, 1.0)

    def _project_of(self, root: Path, p: Path) -> str:
        try:
            rel = p.relative_to(root)
            return rel.parts[0] if len(rel.parts) > 1 else root.name
        except Exception:
            return root.name
