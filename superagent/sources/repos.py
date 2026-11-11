from __future__ import annotations

import json
import math
from datetime import date, timedelta
from urllib.parse import quote_plus

from .. import util
from . import keyword_in


def _gh_search(query: str, per_page: int = 10, timeout: int = 30) -> list[dict]:
    endpoint = "search/repositories?q=" + quote_plus(query)
    endpoint += f"&sort=stars&order=desc&per_page={per_page}"
    rc, out, err = util.run_capture(["gh", "api", endpoint], timeout=timeout)
    if rc != 0:
        detail = (err or out or "").strip().splitlines()
        raise RuntimeError("gh failed: " + (detail[-1] if detail else f"exit {rc}"))
    data = json.loads(out)
    return data.get("items") or []


def normalize(item: dict, query: str = "") -> dict:
    topics = item.get("topics") or []
    return {
        "kind": "repo",
        "key": (item.get("full_name") or "").lower(),
        "name": item.get("full_name") or "",
        "url": item.get("html_url") or "",
        "description": (item.get("description") or "").strip(),
        "stars": int(item.get("stargazers_count") or 0),
        "language": item.get("language") or "",
        "pushed": (item.get("pushed_at") or "")[:10],
        "updated": (item.get("updated_at") or "")[:10],
        "topics": topics[:10],
        "query": query,
    }


def score(repo: dict, keywords: list[str]) -> tuple[float, list[str]]:
    hay = " ".join([repo.get("name") or "", repo.get("description") or "",
                    " ".join(repo.get("topics") or [])]).lower()
    matched = []
    value = 0.0
    for kw in keywords:
        k = (kw or "").strip().lower()
        if len(k) >= 3 and keyword_in(k, hay):
            matched.append(kw)
    value += min(len(matched), 4) * 6.0
    stars = repo.get("stars") or 0
    value += min(math.log10(stars + 1) * 1.5, 4.0)
    pushed = repo.get("pushed") or ""
    if pushed:
        try:
            days = (date.today() - date.fromisoformat(pushed)).days
            if days <= 30:
                value += 2.0
            elif days <= 90:
                value += 1.0
        except ValueError:
            pass
    return value, matched


def collect(queries: list[str], keywords: list[str], *, min_stars: int = 10,
            max_age_days: int = 365, per_page: int = 8, exclude: list[str] | None = None,
            timeout: int = 30) -> tuple[list[dict], dict]:
    """Search GitHub repositories for each query. Returns (repos, errors)."""
    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
    exclude = [e.lower() for e in (exclude or []) if e]
    seen: dict[str, dict] = {}
    errors: dict = {}
    from concurrent.futures import ThreadPoolExecutor

    jobs = []
    for query in queries:
        q = query.strip()
        if not q:
            continue
        if min_stars > 0:
            q += f" stars:>={min_stars}"
        q += f" pushed:>={cutoff} archived:false"
        for user in exclude:
            q += f" -user:{user}"
        jobs.append((query, q))

    def run(job):
        query, q = job
        return query, _gh_search(q, per_page=per_page, timeout=timeout)

    batches = []
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(jobs)))) as pool:
        futures = {pool.submit(run, job): job[0] for job in jobs}
        for fut, query in futures.items():
            try:
                batches.append(fut.result())
            except Exception as e:
                errors[query] = f"{type(e).__name__}: {e}"
    for query, items in batches:
        for item in items:
            repo = normalize(item, query)
            if not repo["name"] or repo["key"] in seen:
                continue
            value, matched = score(repo, keywords)
            repo["score"] = value
            repo["matched"] = matched
            seen[repo["key"]] = repo
    repos = list(seen.values())
    repos.sort(key=lambda r: (r["score"], r["stars"]), reverse=True)
    return repos, errors
