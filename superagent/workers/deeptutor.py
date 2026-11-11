from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .. import util
from ..models import WorkerResult
from .base import Worker

CAPABILITY_RULES = [
    ("deep_research", ("调研", "综述", "文献综述", "现状分析", "research", "survey",
                       "literature review")),
    ("mastery_path", ("学习路径", "学习计划", "复习计划", "掌握路径", "mastery")),
    ("visualize", ("可视化", "思维导图", "画图", "diagram", "mind map")),
    ("deep_solve", ("解题", "求解", "推导", "证明", "solve")),
    ("chat", ("讲解", "解释", "是什么", "为什么", "怎么理解")),
    ("deep_question", ("出题", "测验", "练习题", "自测题", "quiz")),
]


class DeepTutorWorker(Worker):
    """DeepTutor CLI worker: tutoring, research, practice and learning paths."""

    name = "deeptutor"
    stdout_from_tail = True
    stdout_limit = 4_000_000

    def conf(self) -> dict:
        return self.cfg.get("workers.deeptutor", {}) or {}

    def _command(self) -> str:
        raw = str(self.conf().get("command") or "deeptutor")
        if Path(raw).exists():
            return raw
        return util.which(raw) or raw

    def _prefix(self) -> list[str]:
        cmd = self._command()
        if sys.platform == "win32" and cmd.lower().endswith((".cmd", ".bat")):
            return ["cmd.exe", "/c", cmd]
        return [cmd]

    def available(self) -> tuple[bool, str]:
        if not self.conf().get("enabled", True):
            return False, "disabled"
        cmd = self._command()
        if not Path(cmd).exists() and not util.which(cmd):
            return False, f"not found: {cmd}"
        return True, ""

    def capability(self, prompt: str) -> str:
        forced = str(self.conf().get("capability") or "auto").strip()
        if forced and forced != "auto":
            return forced
        low = (prompt or "").lower()
        for name, words in CAPABILITY_RULES:
            if any(w.lower() in low for w in words):
                return name
        return "chat"

    def build_argv(self, prompt, workspace, extra=None):
        conf = self.conf()
        # cmd.exe splits command lines on newlines; the .cmd launcher would truncate the prompt.
        message = re.sub(r"\s*\n+\s*", " ", prompt or "").strip()[:24000]
        argv = self._prefix() + [
            "run", self.capability(prompt), message,
            "-l", str(conf.get("language") or "zh"),
            "--format", "json",
        ]
        argv += list(conf.get("extra_args") or [])
        return argv

    def parse_output(self, stdout, stderr, rc) -> WorkerResult:
        result = None
        done = None
        chunks = []
        errors = []
        session_id = ""
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("session_id"):
                session_id = str(ev["session_id"])
            kind = ev.get("type")
            if kind == "result":
                result = ev
            elif kind == "done":
                done = ev
            elif kind == "content":
                meta = ev.get("metadata") or {}
                if ev.get("stage") == "responding" and meta.get("trace_kind") == "llm_chunk":
                    text = ev.get("content") or ""
                    if text:
                        chunks.append(text)
            elif kind in ("error", "failed", "turn_error"):
                errors.append(str(ev.get("content") or ev.get("message") or "")[:500])

        text = ""
        usage = {}
        if isinstance(result, dict):
            meta = result.get("metadata") or {}
            text = str(meta.get("response") or "")
            inner = meta.get("metadata") or {}
            cost = inner.get("cost_summary") or {}
            tokens = inner.get("usage_summary") or {}
            usage.update({
                "cost_usd": round(float(cost.get("total_cost_usd") or 0), 6),
                "total_tokens": int(cost.get("total_tokens") or tokens.get("total_tokens") or 0),
                "prompt_tokens": int(cost.get("prompt_tokens") or tokens.get("prompt_tokens") or 0),
                "completion_tokens": int(cost.get("completion_tokens") or tokens.get("completion_tokens") or 0),
                "num_turns": meta.get("rounds"),
            })
            if meta.get("completed") is False:
                errors.append("DeepTutor reported an incomplete turn")
        if not text and chunks:
            text = "".join(chunks).strip()
        if isinstance(done, dict):
            meta = done.get("metadata") or {}
            summary = meta.get("usage_summary") or {}
            if summary:
                usage.setdefault("total_tokens", int(summary.get("total_tokens") or 0))
                usage.setdefault("prompt_tokens", int(summary.get("prompt_tokens") or 0))
                usage.setdefault("completion_tokens", int(summary.get("completion_tokens") or 0))
        if session_id:
            usage["session_id"] = session_id

        if not text:
            if not errors and (stdout or "").strip():
                errors.append("unparsed DeepTutor output (expected JSONL); see stdout.log")
            detail = "\n".join(errors) or (stderr or "")[-1200:] or f"deeptutor exit {rc}"
            return WorkerResult(ok=False, error=util.truncate(detail, 1000),
                                stdout=stdout, usage=usage)

        res = WorkerResult(ok=True, summary=util.truncate(text, 1500), stdout=text)
        res.usage.update(usage)
        if rc != 0:
            res.ok = False
            res.error = res.error or f"deeptutor exit {rc}"
        return res

    def doctor(self, timeout: int = 30) -> tuple[bool, str]:
        rc, out, err = util.run_capture(self._prefix() + ["doctor"], timeout=timeout)
        text = (out or "") + ("\n" + err if err.strip() else "")
        ok = rc == 0 and "Required checks passed" in text
        if ok:
            return True, "required checks passed"
        tail = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return False, util.truncate(tail[-1] if tail else f"exit {rc}", 300)

    def client_available(self, timeout: float = 2.0) -> tuple[bool, str]:
        url = str(self.conf().get("client_url") or "").rstrip("/")
        if not url:
            return False, "client_url not configured"
        try:
            with urllib.request.urlopen(url + "/health", timeout=timeout) as r:
                return (r.status == 200), f"{url} HTTP {r.status}"
        except urllib.error.HTTPError as e:
            return False, f"{url} HTTP {e.code}"
        except Exception as e:
            return False, f"{url} {type(e).__name__}"
