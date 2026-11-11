from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import util


@dataclass
class Signal:
    source: str
    kind: str
    title: str
    body: str = ""
    url: str = ""
    actor: str = ""
    occurred_at: str = ""
    project: str = ""
    importance: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = util.new_id("sig")
        if not self.occurred_at:
            self.occurred_at = util.utcnow()
        if not self.fingerprint:
            self.fingerprint = util.fingerprint(self.source, self.kind, self.url or self.title, self.title)

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "kind": self.kind,
            "fingerprint": self.fingerprint,
            "title": util.truncate(self.title, 300),
            "body": util.truncate(self.body, 4000),
            "url": self.url,
            "actor": self.actor,
            "occurred_at": self.occurred_at,
            "collected_at": util.utcnow(),
            "raw_json": util.json_dumps(self.raw),
            "project": self.project,
            "importance": float(self.importance),
            "status": "new",
        }


@dataclass
class WorkerResult:
    ok: bool
    summary: str = ""
    artifacts: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)  # proposed external actions needing approval
    usage: dict = field(default_factory=dict)
    error: str = ""
    stdout: str = ""
