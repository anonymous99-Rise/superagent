from __future__ import annotations

import time
from pathlib import Path

from .. import util
from ..models import WorkerResult
from .base import Worker


class LocalWorker(Worker):
    """Deterministic worker for cheap bookkeeping tasks (verify, digest, noop)."""

    name = "local"

    def available(self) -> tuple[bool, str]:
        return True, ""

    def run(self, prompt, workspace, timeout, stdout_path, stderr_path, on_pid=None, extra=None):
        started = time.time()
        extra = extra or {}
        kind = extra.get("local_kind", "noop")
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "verify":
            paths = extra.get("paths") or []
            found, missing = [], []
            for p in paths:
                pp = Path(p)
                if not pp.is_absolute() and workspace:
                    pp = Path(workspace) / pp
                (found if pp.exists() else missing).append(str(pp))
            ok = bool(found) and not missing
            summary = f"校验完成：存在 {len(found)} 项，缺失 {len(missing)} 项。"
            if missing:
                summary += "\n缺失：" + ", ".join(missing[:10])
            artifacts = [{"path": p, "title": Path(p).name, "kind": "verified"} for p in found]
            res = WorkerResult(ok=ok, summary=summary, artifacts=artifacts,
                               usage={"duration_ms": int((time.time() - started) * 1000)})
        elif kind == "digest":
            text = extra.get("text", "")
            res = WorkerResult(ok=True, summary=util.truncate(text, 3000))
        else:
            res = WorkerResult(ok=True, summary=util.truncate(prompt, 1500))
        stdout_path.write_text(res.summary, encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return res
