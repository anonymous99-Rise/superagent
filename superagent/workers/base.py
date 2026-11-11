from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .. import util
from ..models import WorkerResult

JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


class Worker:
    name = "base"

    def __init__(self, cfg):
        self.cfg = cfg

    def conf(self) -> dict:
        return self.cfg.get(f"workers.{self.name}", {}) or {}

    def available(self) -> tuple[bool, str]:
        return True, ""

    def build_argv(self, prompt: str, workspace: str, extra: dict | None = None) -> list[str]:
        raise NotImplementedError

    def parse_output(self, stdout: str, stderr: str, rc: int) -> WorkerResult:
        raise NotImplementedError

    def run(self, prompt: str, workspace: str, timeout: int, stdout_path: Path,
            stderr_path: Path, on_pid=None, extra: dict | None = None) -> WorkerResult:
        argv = self.build_argv(prompt, workspace, extra)
        env = dict(os.environ)
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("NO_COLOR", "1")
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.time()
        p = None
        try:
            with open(stdout_path, "w", encoding="utf-8", errors="replace") as fo, \
                 open(stderr_path, "w", encoding="utf-8", errors="replace") as fe:
                kwargs = {}
                if sys.platform == "win32":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                p = subprocess.Popen(
                    argv, cwd=workspace or None, stdin=subprocess.DEVNULL,
                    stdout=fo, stderr=fe, env=env, text=True, **kwargs,
                )
                if on_pid:
                    on_pid(p.pid)
                try:
                    rc = p.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    util.kill_tree(p.pid)
                    try:
                        p.wait(timeout=15)
                    except Exception:
                        pass
                    stdout = util.read_text(stdout_path)
                    stderr = util.read_text(stderr_path)
                    res = self.parse_output(stdout, stderr, 124)
                    res.ok = False
                    res.error = res.error or f"timeout after {timeout}s"
                    return res
        except FileNotFoundError:
            return WorkerResult(ok=False, error=f"worker executable not found: {argv[0]}")
        except Exception as e:
            return WorkerResult(ok=False, error=f"{type(e).__name__}: {e}")
        stdout = util.read_text(stdout_path)
        stderr = util.read_text(stderr_path)
        res = self.parse_output(stdout, stderr, rc)
        res.usage.setdefault("duration_ms", int((time.time() - started) * 1000))
        if rc != 0 and res.ok:
            res.ok = False
            res.error = res.error or f"exit code {rc}"
        return res

    # ---- parsing helpers ----
    def _extract_json(self, text: str) -> dict | None:
        if not text:
            return None
        for m in JSON_FENCE.finditer(text):
            try:
                return json.loads(m.group(1))
            except Exception:
                continue
        stripped = text.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except Exception:
                pass
        # last balanced object
        end = stripped.rfind("}")
        if end != -1:
            depth = 0
            for i in range(end, -1, -1):
                if stripped[i] == "}":
                    depth += 1
                elif stripped[i] == "{":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(stripped[i:end + 1])
                        except Exception:
                            break
        return None

    def _structured(self, text: str) -> WorkerResult:
        obj = self._extract_json(text)
        if not isinstance(obj, dict):
            return WorkerResult(ok=True, summary=util.truncate(text, 1500), stdout=text)
        summary = obj.get("summary") or obj.get("result") or obj.get("message") or ""
        artifacts = obj.get("artifacts") if isinstance(obj.get("artifacts"), list) else []
        actions = obj.get("actions") if isinstance(obj.get("actions"), list) else []
        needs_human = bool(obj.get("needs_human"))
        ok = obj.get("ok", True) is not False
        return WorkerResult(
            ok=ok, summary=util.truncate(summary, 2000),
            artifacts=[a for a in artifacts if isinstance(a, dict)][:20],
            actions=[a for a in actions if isinstance(a, dict)][:20],
            usage={"needs_human": needs_human}, stdout=text,
        )

    def detect_artifacts(self, workspace: str, since_ts: float) -> list[dict]:
        if not workspace:
            return []
        root = Path(workspace)
        if not root.exists():
            return []
        out = []
        skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "var"}
        # BLAST index components (db.pdb/db.phr/...) are build byproducts, not deliverables
        index_files = {"db.pdb", "db.phr", "db.pin", "db.psq", "db.ptf", "db.pto", "db.pot", "db.pjs"}
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            for fn in filenames:
                if fn.lower() in index_files:
                    continue
                p = Path(dirpath) / fn
                try:
                    if p.stat().st_mtime >= since_ts:
                        out.append({"path": str(p), "title": fn, "kind": "file"})
                        count += 1
                except OSError:
                    continue
            if count >= 50:
                break
        return out
