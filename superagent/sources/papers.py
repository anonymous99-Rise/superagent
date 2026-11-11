from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from urllib.parse import quote_plus, urlencode

from . import (clean_text, get_json, get_text, iso_date, paper_key, pubmed_date,
               score_paper, within)


def _search_terms(keywords: list[str]) -> list[str]:
    seen = []
    for kw in keywords:
        kw = (kw or "").strip()
        if len(kw) >= 2 and kw.lower() not in (x.lower() for x in seen):
            seen.append(kw)
    return seen[:12]


def fetch_pubmed(keywords: list[str], days: int = 7, limit: int = 40,
                 timeout: int = 30) -> list[dict]:
    terms = _search_terms(keywords)
    if not terms:
        return []
    end = date.today()
    start = end - timedelta(days=days)
    query = " OR ".join(f'"{t}"[tiab]' for t in terms)
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed"
        "&retmode=json&sort=date&retmax=%d&term=%s&mindate=%s&maxdate=%s&datetype=pdat"
        % (limit, quote_plus(query), start.isoformat(), end.isoformat())
    )
    data = get_json(url, timeout=timeout)
    ids = (data.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return []
    fetch_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed"
        "&retmode=xml&id=" + ",".join(ids)
    )
    root = ET.fromstring(get_text(fetch_url, timeout=timeout))
    out = []
    for art in root.findall(".//PubmedArticle"):
        citation = art.find("MedlineCitation")
        article = citation.find("Article") if citation is not None else None
        if article is None:
            continue
        pmid = (citation.findtext("PMID") or "").strip()
        title = clean_text("".join(article.find("ArticleTitle").itertext())) \
            if article.find("ArticleTitle") is not None else ""
        if not title or title.lower().startswith(("correction", "erratum", "retraction")):
            continue
        abstract_parts = []
        for node in article.findall("./Abstract/AbstractText"):
            label = (node.get("Label") or "").strip()
            text = clean_text("".join(node.itertext()), limit=1200)
            if text:
                abstract_parts.append(f"{label}: {text}" if label else text)
        abstract = " ".join(abstract_parts)
        journal = (article.findtext("./Journal/Title")
                   or article.findtext("./Journal/ISOAbbreviation") or "").strip()
        published = pubmed_date(article.find("./Journal/JournalIssue/PubDate"))
        doi = ""
        for node in art.findall(".//ArticleIdList/ArticleId"):
            if (node.get("IdType") or "").lower() == "doi":
                doi = (node.text or "").strip()
                break
        if not doi:
            for node in article.findall("./ELocationID"):
                if (node.get("EIdType") or "").lower() == "doi":
                    doi = (node.text or "").strip()
                    break
        authors = []
        for a in article.findall("./AuthorList/Author")[:6]:
            name = (a.findtext("CollectiveName") or "").strip()
            if not name:
                last = (a.findtext("LastName") or "").strip()
                initials = (a.findtext("Initials") or "").strip()
                name = (last + " " + initials).strip()
            if name:
                authors.append(name)
        out.append({
            "source": "pubmed",
            "source_id": pmid,
            "title": title,
            "abstract": abstract,
            "journal": journal,
            "published": published,
            "doi": doi,
            "authors": authors,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        })
    for p in out:
        p["key"] = paper_key(p)
    return [p for p in out if within(p["published"], days)]


def fetch_arxiv(keywords: list[str], days: int = 7, limit: int = 40,
                timeout: int = 35) -> list[dict]:
    terms = _search_terms(keywords)
    if not terms:
        return []
    query = " OR ".join(f'all:"{t}"' for t in terms)
    url = ("http://export.arxiv.org/api/query?" + urlencode({
        "search_query": query, "start": 0, "max_results": limit,
        "sortBy": "submittedDate", "sortOrder": "descending",
    }))
    xml = get_text(url, timeout=timeout, accept="application/atom+xml")
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(xml)
    out = []
    for entry in root.findall("a:entry", ns):
        title = clean_text(entry.findtext("a:title", default="", namespaces=ns))
        if not title or title.lower().startswith(("correction", "erratum", "retraction")):
            continue
        abstract = clean_text(entry.findtext("a:summary", default="", namespaces=ns),
                              limit=1200)
        published = iso_date(entry.findtext("a:published", default="", namespaces=ns))
        link = ""
        for l in entry.findall("a:link", ns):
            if l.get("rel") == "alternate":
                link = l.get("href") or ""
                break
        doi = (entry.findtext("arxiv:doi", default="", namespaces=ns) or "").strip()
        journal = (entry.findtext("arxiv:journal_ref", default="", namespaces=ns) or "").strip()
        authors = [clean_text(a.findtext("a:name", default="", namespaces=ns), limit=80)
                   for a in entry.findall("a:author", ns)[:6]]
        out.append({
            "source": "arxiv",
            "source_id": entry.findtext("a:id", default="", namespaces=ns),
            "title": title,
            "abstract": abstract,
            "journal": journal or "arXiv",
            "published": published,
            "doi": doi,
            "authors": [a for a in authors if a],
            "url": link,
        })
    for p in out:
        p["key"] = paper_key(p)
    return [p for p in out if within(p["published"], days)]


def fetch_europepmc(keywords: list[str], days: int = 7, limit: int = 40,
                    timeout: int = 30) -> list[dict]:
    terms = _search_terms(keywords)
    if not terms:
        return []
    end = date.today()
    start = end - timedelta(days=days)
    query = ("(" + " OR ".join(f'TITLE_ABS:"{t}"' for t in terms) + ")"
             f" AND (FIRST_PDATE:[{start.isoformat()} TO {end.isoformat()}])")
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urlencode({
        "query": query, "format": "json", "resultType": "core",
        "pageSize": limit, "sort": "P_PDATE_D desc",
    }))
    data = get_json(url, timeout=timeout)
    results = ((data.get("resultList") or {}).get("result") or [])
    out = []
    for r in results:
        title = clean_text(r.get("title") or "")
        if not title or title.lower().startswith(("correction", "erratum", "retraction")):
            continue
        abstract = clean_text(r.get("abstractText") or "", limit=1200)
        doi = (r.get("doi") or "").strip()
        rid = (r.get("id") or "").strip()
        src = (r.get("source") or "").strip()
        journal = (r.get("journalTitle") or "").strip()
        if not journal:
            journal = (((r.get("journalInfo") or {}).get("journal") or {}).get("title") or "").strip()
        if not journal:
            journal = "preprint" if src.upper() == "PPR" else "Europe PMC"
        published = iso_date(r.get("firstPublicationDate") or "")
        authors = [a.strip() for a in (r.get("authorString") or "").split(",")[:6] if a.strip()]
        if doi:
            link = "https://doi.org/" + doi
        elif rid:
            link = f"https://europepmc.org/article/{src}/{rid}"
        else:
            link = ""
        out.append({
            "source": "europepmc",
            "source_id": rid,
            "title": title,
            "abstract": abstract,
            "journal": journal,
            "published": published,
            "doi": doi,
            "authors": authors,
            "url": link,
        })
    for p in out:
        p["key"] = paper_key(p)
    return [p for p in out if within(p["published"], days)]


FETCHERS = {
    "pubmed": fetch_pubmed,
    "arxiv": fetch_arxiv,
    "europepmc": fetch_europepmc,
}


def collect(keywords: list[str], sources: list[str], days: int, limit: int = 40,
            timeout: int = 30) -> tuple[list[dict], dict, dict]:
    """Fetch and score papers from all sources. Returns (papers, errors, source_counts)."""
    papers: list[dict] = []
    errors: dict = {}
    source_counts: dict = {}
    by_key: dict[str, dict] = {}
    from concurrent.futures import ThreadPoolExecutor

    jobs = {name: FETCHERS[name] for name in sources if name in FETCHERS}
    for name in sources:
        if name not in FETCHERS:
            errors[name] = "unknown source"

    def run(name):
        return name, jobs[name](keywords, days=days, limit=limit, timeout=timeout)

    with ThreadPoolExecutor(max_workers=max(1, min(4, len(jobs)))) as pool:
        futures = {pool.submit(run, name): name for name in jobs}
        for fut in futures:
            name = futures[fut]
            try:
                name, batch = fut.result()
                source_counts[name] = len(batch)
            except Exception as e:
                errors[name] = f"{type(e).__name__}: {e}"
                continue
            for p in batch:
                score, matched = score_paper(p, keywords)
                p["score"] = score
                p["matched"] = matched
                key = p.get("key") or paper_key(p)
                p["key"] = key
                old = by_key.get(key)
                if old is None:
                    by_key[key] = p
                    continue
                # Prefer the record with an abstract / higher score.
                if (p.get("abstract") and not old.get("abstract")) or p["score"] > old["score"]:
                    by_key[key] = p
    papers = list(by_key.values())
    papers.sort(key=lambda p: p.get("published") or "", reverse=True)
    papers.sort(key=lambda p: p["score"], reverse=True)
    return papers, errors, source_counts
