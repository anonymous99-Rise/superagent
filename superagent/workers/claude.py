from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .. import util
from ..models import WorkerResult
from .base import Worker


def resolve_exe(command: str) -> str:
    """Resolve an npm-style shim to a real executable on Windows."""
    if Path(command).is_file() and command.lower().endswith(".exe"):
        return command
    found = shutil.which(command)
    if not found:
        return command
    if found.lower().endswith((".cmd", ".bat")):
        try:
            text = Path(found).read_text(encoding="utf-8", errors="replace")
            m = re.search(r'"([^"]+\.exe)"', text)
            if m:
                target = m.group(1)
                parent = str(Path(found).parent)
                target = re.sub(r"%~?dp0%?", lambda _m: parent, target, flags=re.I)
                if Path(target).exists():
                    return target
        except OSError:
            pass
    return found


class ClaudeWorker(Worker):
    name = "claude"

    def available(self) -> tuple[bool, str]:
        if not self.conf().get("enabled", True):
            return False, "disabled"
        cmd = self.conf().get("command", "claude")
        if not util.which(cmd) and not shutil.which(cmd):
            return False, f"not found: {cmd}"
        return True, ""

    def build_argv(self, prompt, workspace, extra=None):
        conf = self.conf()
        argv = [resolve_exe(conf.get("command", "claude")), "-p", "--output-format", "json",
                "--dangerously-skip-permissions"]
        if conf.get("model"):
            argv += ["--model", conf["model"]]
        argv += list(conf.get("extra_args") or [])
        # NOTE: the prompt must come last; --add-dir is variadic and would swallow it.
        argv.append(prompt[:24000])
        return argv

    def parse_output(self, stdout, stderr, rc) -> WorkerResult:
        obj = None
        try:
            obj = json.loads(stdout)
        except Exception:
            obj = None
        if not isinstance(obj, dict):
            res = self._structured(stdout)
            if rc != 0:
                res.ok = False
                res.error = res.error or util.truncate(stderr, 500)
            return res
        text = obj.get("result") or ""
        is_error = bool(obj.get("is_error")) or obj.get("subtype") not in (None, "success")
        res = self._structured(text or stdout)
        res.ok = not is_error and res.ok
        res.usage = dict(res.usage or {})
        res.usage["num_turns"] = obj.get("num_turns")
        res.usage["duration_ms"] = obj.get("duration_ms")
        cost = 0.0
        for _, m in (obj.get("modelUsage") or {}).items():
            try:
                cost += float(m.get("costUSD") or 0)
            except Exception:
                pass
        res.usage["cost_usd"] = round(cost, 6)
        if is_error:
            res.error = res.error or util.truncate(text or stderr or "claude reported an error", 800)
        return res
