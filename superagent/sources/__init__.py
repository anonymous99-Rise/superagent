from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

USER_AGENT = "SuperAgent/0.1 (personal research digest; local tool)"


def _get(url: str, timeout: int = 25, accept: str = "application/json") -> str:
    last = None
    for attempt in range(2):
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": accept,
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(400).decode("utf-8", "replace")
            except Exception:
                pass
            last = RuntimeError(f"HTTP {e.code} {url[:160]} {detail}")
            if e.code < 500 and e.code != 429:
                break
        except Exception as e:
            last = RuntimeError(f"{type(e).__name__}: {e}")
        if attempt == 0:
            time.sleep(1.5)
    raise last or RuntimeError("request failed")


def get_json(url: str, timeout: int = 25):
    return json.loads(_get(url, timeout=timeout))


def get_text(url: str, timeout: int = 25, accept: str = "*/*") -> str:
    return _get(url, timeout=timeout, accept=accept)


def iso_date(value: str) -> str:
    """Best-effort ISO date (YYYY-MM-DD / YYYY-MM / YYYY) from an API string."""
    value = (value or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d",
                "%Y %b %d", "%Y %b", "%Y-%m", "%Y", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value[:len(value)], fmt).date().isoformat()
        except ValueError:
            continue
    digits = "".join(ch if ch.isdigit() else "-" for ch in value[:10]).strip("-")
    parts = [p for p in digits.split("-") if p]
    if parts and len(parts[0]) == 4:
        year = parts[0]
        month = parts[1] if len(parts) > 1 else "01"
        day = parts[2] if len(parts) > 2 else "01"
        try:
            return date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            return f"{year}-01-01"
    return ""


MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]) if m}


def pubmed_date(pub_date) -> str:
    if pub_date is None:
        return ""
    year = pub_date.findtext("Year") or ""
    month = (pub_date.findtext("Month") or "").strip()
    day = (pub_date.findtext("Day") or "").strip()
    if not year:
        medline = pub_date.findtext("MedlineDate") or ""
        year = medline[:4]
        month = ""
    if not year:
        return ""
    m = month[:3].lower()
    if m in MONTHS:
        month_num = MONTHS[m]
    else:
        try:
            month_num = int(month) if month else 1
        except ValueError:
            month_num = 1
    try:
        day_num = int(day) if day else 1
        return date(int(year), month_num, day_num).isoformat()
    except ValueError:
        return f"{year}-01-01"


def within(published: str, days: int, today: date | None = None) -> bool:
    """True when a date is inside the lookback window or unparseable."""
    if not published:
        return True
    try:
        d = date.fromisoformat(published[:10])
    except ValueError:
        return True
    today = today or date.today()
    return d >= today - timedelta(days=days + 2)


def normalize_title(title: str) -> str:
    import re

    return re.sub(r"[^a-z0-9一-鿿]+", " ", (title or "").lower()).strip()


def paper_key(paper: dict) -> str:
    doi = (paper.get("doi") or "").lower()
    if doi:
        return "doi:" + doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return "title:" + normalize_title(paper.get("title") or "")[:160]


def keyword_in(keyword: str, haystack: str) -> bool:
    """Substring match, but ASCII keywords respect word boundaries."""
    import re

    k = (keyword or "").strip().lower()
    if not k:
        return False
    if re.search(r"[a-z0-9]", k):
        return re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])",
                         haystack) is not None
    return k in haystack


def score_paper(paper: dict, keywords: list[str]) -> tuple[float, list[str]]:
    title = (paper.get("title") or "").lower()
    abstract = (paper.get("abstract") or "").lower()
    score = 0.0
    matched = []
    for kw in keywords:
        k = (kw or "").strip().lower()
        if len(k) < 2:
            continue
        if keyword_in(k, title):
            score += 4.0
            matched.append(kw)
        elif keyword_in(k, abstract):
            score += 1.5
            matched.append(kw)
    return score, matched


def clean_text(text: str, limit: int = 700) -> str:
    import html
    import re

    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?，。；：！？])", r"\1", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:") + "…"
