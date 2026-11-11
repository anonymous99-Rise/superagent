from __future__ import annotations

import json
from pathlib import Path

from .. import util
from ..models import WorkerResult
from .base import Worker


class CodexWorker(Worker):
    name = "codex"

    def available(self) -> tuple[bool, str]:
        if not self.conf().get("enabled", True):
            return False, "disabled"
        cmd = self.conf().get("command") or ""
        if not cmd:
            return False, "command not configured/detected"
        if not Path(cmd).exists() and not util.which(cmd):
            return False, f"not found: {cmd}"
        return True, ""

    def build_argv(self, prompt, workspace, extra=None):
        conf = self.conf()
        last_msg = ""
        if extra and extra.get("last_message_path"):
            last_msg = extra["last_message_path"]
        argv = [conf.get("command"), "exec", "--skip-git-repo-check", "--json"]
        if workspace:
            argv += ["-C", workspace]
        if last_msg:
            argv += ["-o", last_msg]
        sandbox = (extra or {}).get("sandbox") or conf.get("sandbox") or "danger-full-access"
        argv += ["-s", sandbox]
        if conf.get("model"):
            argv += ["-m", conf["model"]]
        argv += list(conf.get("extra_args") or [])
        argv.append(prompt[:24000])
        return argv

    def parse_output(self, stdout, stderr, rc) -> WorkerResult:
        texts = []
        errors = []
        usage = {}
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            t = ev.get("type")
            if t == "item.completed":
                item = ev.get("item") or {}
                if item.get("type") == "agent_message" and item.get("text"):
                    texts.append(item["text"])
                elif item.get("type") == "error":
                    errors.append(item.get("message", ""))
            elif t == "turn.completed":
                usage.update(ev.get("usage") or {})
            elif t in ("error", "turn.failed"):
                errors.append(json.dumps(ev, ensure_ascii=False)[:500])
        text = "\n".join(texts).strip()
        if not text:
            return WorkerResult(
                ok=False, error=util.truncate("\n".join(errors) or stderr or f"codex exit {rc}", 1000),
                stdout=stdout, usage=usage,
            )
        res = self._structured(text)
        res.usage.update(usage)
        if rc != 0 and not errors:
            res.ok = False
            res.error = f"codex exit {rc}"
        if errors and not res.ok:
            res.error = res.error or util.truncate("\n".join(errors), 800)
        return res
