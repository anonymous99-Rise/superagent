from __future__ import annotations

import time
from pathlib import Path

from ..models import Signal


class Collector:
    name = "base"
    label = "Base"
    description = ""

    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.unavailable_reason = ""

    def conf(self) -> dict:
        return self.cfg.get(f"collectors.{self.name}", {}) or {}

    def enabled(self) -> bool:
        return bool(self.conf().get("enabled", False))

    def available(self) -> tuple[bool, str]:
        if not self.enabled():
            return False, "disabled in config"
        return True, ""

    def collect(self) -> list[Signal]:  # pragma: no cover - interface
        raise NotImplementedError

    # helpers
    def _mtime_iso(self, path: Path) -> str:
        from datetime import datetime, timezone

        try:
            ts = path.stat().st_mtime
            return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except OSError:
            return ""

    def _recent(self, path: Path, days: int) -> bool:
        try:
            return (time.time() - path.stat().st_mtime) <= days * 86400
        except OSError:
            return False
