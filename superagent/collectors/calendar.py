from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import Signal
from .base import Collector

DT_RE = re.compile(r"DTSTART[^:]*:([0-9T:Z]+)")
SUMMARY_RE = re.compile(r"SUMMARY:(.+)")
LOC_RE = re.compile(r"LOCATION:(.+)")


class CalendarCollector(Collector):
    name = "calendar"
    label = "日历 (ICS)"
    description = "读取本地 .ics 日历，识别临近会议与截止日期"

    def available(self) -> tuple[bool, str]:
        ok, reason = super().available()
        if not ok:
            return ok, reason
        paths = self.conf().get("paths", [])
        if not any(Path(p).exists() for p in paths):
            return False, "no calendar path configured/existing"
        return True, ""

    def collect(self) -> list[Signal]:
        conf = self.conf()
        lookahead = int(conf.get("lookahead_days", 14))
        out: list[Signal] = []
        now = datetime.now(timezone.utc)
        for base in conf.get("paths", []):
            root = Path(base)
            if not root.exists():
                continue
            files = [root] if root.suffix.lower() == ".ics" else list(root.rglob("*.ics"))
            for f in files:
                out.extend(self._parse(f, now, lookahead))
        return out

    def _parse(self, path: Path, now: datetime, lookahead: int) -> list[Signal]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        text = text.replace("\r\n ", "").replace("\r\n\t", "")
        out = []
        for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
            m_sum = SUMMARY_RE.search(block)
            m_dt = DT_RE.search(block)
            if not m_sum or not m_dt:
                continue
            dt = self._parse_dt(m_dt.group(1))
            if not dt:
                continue
            if not (now - timedelta(hours=1) <= dt <= now + timedelta(days=lookahead)):
                continue
            loc = LOC_RE.search(block)
            title = m_sum.group(1).strip()
            out.append(Signal(
                source=self.name, kind="calendar_event", title=f"日程：{title[:100]}",
                body=f"时间：{dt.isoformat()}\n地点：{loc.group(1).strip() if loc else ''}\n日历：{path.name}",
                url=str(path), occurred_at=dt.isoformat(timespec="seconds").replace("+00:00", "Z"),
                importance=0.65, raw={"summary": title, "start": dt.isoformat()},
            ))
        return out

    def _parse_dt(self, value: str):
        value = value.strip()
        try:
            if value.endswith("Z"):
                return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if "T" in value:
                return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None
