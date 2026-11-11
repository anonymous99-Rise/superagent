import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def sha(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def fingerprint(*parts: str) -> str:
    return sha(*parts)[:32]


def slug(text: str, limit: int = 48) -> str:
    text = re.sub(r"\s+", "-", (text or "").strip().lower())
    text = re.sub(r"[^a-z0-9一-鿿\-_]+", "", text)
    return (text or "x")[:limit].strip("-") or "x"


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def json_loads(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def truncate(text, limit: int = 1200) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def read_text(path: Path, limit: int = 200_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)
    os.replace(tmp, path)


def which(cmd: str):
    from shutil import which as _which

    return _which(cmd)


def run_capture(argv, timeout: int = 60, cwd=None, env=None):
    """Run a command without a shell. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return 124, out, err + "\n[timeout]"
    except FileNotFoundError:
        return 127, "", f"command not found: {argv[0]}"
    except Exception as e:  # pragma: no cover - defensive
        return 1, "", f"{type(e).__name__}: {e}"


def kill_tree(pid: int) -> None:
    if not pid:
        return
    if sys.platform == "win32":
        run_capture(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=20)
    else:  # pragma: no cover
        try:
            os.killpg(os.getpgid(pid), 9)
        except Exception:
            pass


def human_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def days_ago_iso(days: int) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")


def now_ts() -> float:
    return time.time()
