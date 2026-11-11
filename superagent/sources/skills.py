from __future__ import annotations

import re
from pathlib import Path

from .. import util
from . import keyword_in

USEFUL = [
    "paper", "manuscript", "writing", "literature", "citation", "pubmed", "research",
    "academic", "bioinformatics", "phylogen", "statistics", "figure", "plot", "review",
    "translation", "zotero", "experiment", "data analysis", "ppt", "slide", "draw.io",
    "protein", "sequence", "blast", "genome", "annotation", "latex", "reference",
    "论文", "文献", "写作", "科研", "统计", "绘图", "翻译", "综述", "实验",
]


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fields: dict = {}
    current = ""
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            current = m.group(1).lower()
            fields[current] = m.group(2).strip()
        elif current and line.startswith((" ", "\t")):
            fields[current] = (fields.get(current, "") + " " + line.strip()).strip()
    for key, value in list(fields.items()):
        if value in (">", "|", ">-", "|-"):
            fields[key] = ""
        else:
            fields[key] = value.strip().strip('"').strip("'")
    return fields


def parse_skill(path: Path) -> dict | None:
    text = util.read_text(path, limit=12000)
    if not text.strip():
        return None
    fields = _frontmatter(text)
    name = (fields.get("name") or path.parent.name).strip()
    desc = re.sub(r"\s+", " ", fields.get("description") or "").strip()
    if not desc:
        body = re.sub(r"^---.*?\n---", "", text, flags=re.S)
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if len(line) >= 20 and not line.startswith(("```", "|", ">")):
                desc = line
                break
    if len(desc) > 260:
        desc = desc[:257].rstrip() + "…"
    return {
        "kind": "skill", "key": "skill:" + name.lower(), "name": name,
        "description": desc, "path": str(path), "source": "local",
    }


def scan_skills(roots: list[str], max_files: int = 400) -> list[dict]:
    found: list[dict] = []
    count = 0
    for root in roots:
        if not root:
            continue
        base = Path(root).expanduser()
        if base.is_file():
            files = [base]
        elif base.is_dir():
            files = []
            for p in base.rglob("SKILL.md"):
                if "node_modules" in p.parts or ".git" in p.parts:
                    continue
                files.append(p)
                if len(files) >= max_files:
                    break
        else:
            continue
        for f in sorted(files):
            count += 1
            if count > max_files:
                break
            try:
                skill = parse_skill(f)
            except Exception:
                continue
            if skill and skill["name"]:
                found.append(skill)
    # Prefer skills/ over vendor copies of the same name.
    dedup: dict[str, dict] = {}
    for s in found:
        old = dedup.get(s["key"])
        if old is None or ("vendor" in old["path"].lower() and "vendor" not in s["path"].lower()):
            dedup[s["key"]] = s
    return list(dedup.values())


def score(skill: dict, keywords: list[str]) -> tuple[float, list[str]]:
    hay = (skill["name"] + " " + skill["description"]).lower()
    value = 0.0
    matched = []
    for kw in keywords:
        k = (kw or "").strip().lower()
        if len(k) >= 3 and keyword_in(k, hay):
            value += 3.0
            matched.append(kw)
    for kw in USEFUL:
        if keyword_in(kw, hay):
            value += 1.0
            if kw not in matched:
                matched.append(kw)
    return value, matched


def collect(roots: list[str], keywords: list[str], limit: int = 30) -> tuple[list[dict], dict]:
    errors: dict = {}
    try:
        skills = scan_skills(roots)
    except Exception as e:
        errors["skills"] = f"{type(e).__name__}: {e}"
        return [], errors
    out = []
    for s in skills:
        value, matched = score(s, keywords)
        if value <= 0:
            continue
        s = dict(s)
        s["score"] = value
        s["matched"] = matched[:8]
        out.append(s)
    out.sort(key=lambda s: s["score"], reverse=True)
    return out[:limit], errors
