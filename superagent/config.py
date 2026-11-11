from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "core": {
        "data_dir": "var",
        "db_path": "var/super.db",
        "log_level": "INFO",
        "api_host": "127.0.0.1",
        "api_port": 8787,
        "timezone": "Asia/Shanghai",
        "workspace_dir": "D:/devProject",
    },
    "patrol": {
        "enabled": True,
        "interval_sec": 900,
        "run_on_start": True,
        "jitter_sec": 30,
    },
    "policy": {
        "external_requires_approval": True,
        "destructive_requires_approval": True,
        "max_parallel_tasks": 3,
        "default_timeout_sec": 900,
        "max_attempts": 2,
        "auto_plan": True,
        "auto_plan_per_cycle": 2,
        "max_pending_backlog": 12,
        "auto_run_max_level": 4,
        "daily_cost_budget_usd": 5.0,
    },
    "workers": {
        "claude": {
            "enabled": True,
            "command": "claude",
            "extra_args": [],
            "timeout_sec": 900,
            "model": "",
        },
        "codex": {
            "enabled": True,
            "command": "",
            "extra_args": [],
            "timeout_sec": 900,
            "model": "",
        },
        "deeptutor": {
            "enabled": True,
            "command": "deeptutor",
            "extra_args": [],
            "timeout_sec": 600,
            "language": "zh",
            "capability": "auto",
            "client_url": "http://127.0.0.1:18801",
        },
    },
    "notify": {
        "desktop": True,
        "min_priority": 2,
        "cooldown_sec": 300,
    },
    "digest": {
        "enabled": True,
        "time": "08:00",
        "notify": True,
        "lookback_days": 7,
        "max_papers": 8,
        "max_per_source": 4,
        "min_paper_score": 3.0,
        "source_limit": 40,
        "sources": ["pubmed", "europepmc", "arxiv"],
        "keywords": [
            "photosystem I", "PsaE", "photosystem II", "cyanobacteria", "Synechocystis",
            "chloroplast", "plastid genome", "photosynthesis", "phylogenomics", "ycf51",
            "plastid annotation",
        ],
        "max_skills": 4,
        "skill_cooldown_days": 14,
        "skill_dirs": ["~/.dsh/skills", "~/.dsh/vendor", "~/.claude/skills"],
        "max_github_skills": 2,
        "github_skills_queries": [
            "topic:claude-skills academic",
            "topic:claude-skills research",
            "academic research agent skills",
        ],
        "github_skills_min_stars": 20,
        "github_skills_max_age_days": 730,
        "github_skills_keywords": [
            "research", "academic", "scholar", "science", "phd", "literature",
            "bioinformatics", "laboratory", "manuscript", "citation",
        ],
        "max_repos": 5,
        "min_repo_stars": 10,
        "repo_max_age_days": 730,
        "repos_per_query": 8,
        "repo_queries": [
            "cyanobacteria", "photosystem", "plastid genome", "chloroplast genome",
            "phylogenetics", "molecular evolution", "bioinformatics pipeline",
        ],
        "exclude_users": ["anonymous99-Rise"],
        "blocked_repo_terms": [
            "z-library", "shadow library", "影子图书馆", "piracy", "盗版",
        ],
        "obsidian_sync": True,
        "obsidian_vault": "D:/devProject/super建议",
    },
    "collectors": {
        "filesystem": {
            "enabled": True,
            "paths": ["C:/Users/27608/Desktop", "C:/Users/27608/Documents"],
            "recency_days": 14,
            "max_files": 500,
            "extensions": [".md", ".txt", ".docx", ".pdf", ".xlsx", ".pptx", ".tex", ".csv", ".ipynb"],
        },
        "git": {"enabled": True, "roots": ["D:/devProject"], "recency_days": 14, "max_repos": 50},
        "github": {"enabled": True, "limit": 30, "include_notifications": True},
        "zotero": {"enabled": True, "db_path": "C:/Users/27608/Zotero/zotero.sqlite", "recency_days": 45},
        "dsh": {"enabled": True, "home": "C:/Users/27608/.dsh", "max_sessions": 40},
        "obsidian": {"enabled": True, "vault": "C:/Users/27608/Documents/Obsidian Vault"},
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _detect_codex() -> str:
    import glob

    candidates = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates += glob.glob(os.path.join(local, "OpenAI", "Codex", "bin", "*", "codex.exe"))
    from shutil import which

    w = which("codex")
    if w:
        return w
    if candidates:
        candidates.sort(key=os.path.getmtime, reverse=True)
        return candidates[0]
    return ""


class Config:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or os.environ.get("SUPER_CONFIG") or (ROOT / "config" / "super.toml"))
        data = {}
        if self.path.exists():
            with open(self.path, "rb") as f:
                data = tomllib.load(f)
        merged = _deep_merge(DEFAULTS, data)
        if not merged["workers"]["codex"]["command"]:
            merged["workers"]["codex"]["command"] = _detect_codex()
        self.data = merged

    def __getitem__(self, key):
        return self.data[key]

    def get(self, dotted: str, default=None):
        cur = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, dotted: str, value) -> None:
        parts = dotted.split(".")
        cur = self.data
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value

    def root(self, *parts) -> Path:
        return ROOT.joinpath(*parts)

    def data_path(self, *parts) -> Path:
        base = Path(self.data["core"]["data_dir"])
        if not base.is_absolute():
            base = ROOT / base
        p = base.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def db_path(self) -> Path:
        p = Path(self.data["core"]["db_path"])
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def load(path: Path | None = None) -> Config:
    return Config(path)


EXAMPLE_TOML = """# SuperAgent configuration. Copy to config/super.toml and edit.
[core]
data_dir = "var"
db_path = "var/super.db"
api_host = "127.0.0.1"
api_port = 8787
workspace_dir = "D:/devProject"

[patrol]
enabled = true
interval_sec = 900

[policy]
external_requires_approval = true
destructive_requires_approval = true
max_parallel_tasks = 3
auto_run_max_level = 4

[workers.claude]
enabled = true
command = "claude"

[workers.codex]
enabled = true
# command auto-detected when empty
command = ""

[workers.deeptutor]
enabled = true
command = "deeptutor"
language = "zh"
capability = "auto"
client_url = "http://127.0.0.1:18801"

[notify]
desktop = true

[collectors.filesystem]
enabled = true
paths = ["C:/Users/<you>/Desktop", "C:/Users/<you>/Documents"]

[collectors.git]
enabled = true
roots = ["D:/devProject"]

[collectors.github]
enabled = true

[collectors.zotero]
enabled = true
db_path = "C:/Users/<you>/Zotero/zotero.sqlite"

[collectors.dsh]
enabled = true
home = "C:/Users/<you>/.dsh"

[collectors.obsidian]
enabled = true
vault = "C:/Users/<you>/Documents/Obsidian Vault"
"""
