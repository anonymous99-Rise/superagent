from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..models import Signal
from .base import Collector


class ZoteroCollector(Collector):
    name = "zotero"
    label = "Zotero 文献库"
    description = "只读巡检 Zotero 新增文献与分类，识别阅读队列与研究主题"

    def available(self) -> tuple[bool, str]:
        ok, reason = super().available()
        if not ok:
            return ok, reason
        p = Path(self.conf().get("db_path", ""))
        if not p.exists():
            return False, f"zotero db not found: {p}"
        try:
            c = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True, timeout=10)
            c.execute("SELECT count(*) FROM items").fetchone()
            c.close()
        except Exception as e:
            return False, f"zotero db unreadable: {e}"
        return True, ""

    def collect(self) -> list[Signal]:
        conf = self.conf()
        days = int(conf.get("recency_days", 45))
        p = Path(conf.get("db_path"))
        out: list[Signal] = []
        try:
            c = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True, timeout=10)
            c.row_factory = sqlite3.Row
        except Exception:
            return out
        try:
            rows = c.execute(
                """
                SELECT i.itemID, it.typeName, i.dateAdded, i.dateModified,
                       v.value AS title,
                       (SELECT group_concat(cr.lastName, ', ')
                          FROM itemCreators ic JOIN creators cr ON cr.creatorID=ic.creatorID
                         WHERE ic.itemID=i.itemID) AS creators,
                       (SELECT group_concat(col.collectionName, ' / ')
                          FROM collectionItems ci JOIN collections col ON col.collectionID=ci.collectionID
                         WHERE ci.itemID=i.itemID) AS collections
                  FROM items i
                  JOIN itemTypes it ON it.itemTypeID=i.itemTypeID
                  LEFT JOIN itemData d ON d.itemID=i.itemID
                       AND d.fieldID=(SELECT fieldID FROM fields WHERE fieldName='title')
                  LEFT JOIN itemDataValues v ON v.valueID=d.valueID
                 WHERE it.typeName NOT IN ('attachment','note','annotation','computerProgram')
                 ORDER BY i.dateAdded DESC LIMIT 100
                """
            ).fetchall()
        except Exception:
            c.close()
            return out
        c.close()
        cutoff = self._cutoff(days)
        for r in rows:
            added = self._iso(r["dateAdded"])
            if added and added < cutoff:
                continue
            title = r["title"] or f"(item {r['itemID']})"
            coll = r["collections"] or ""
            creators = r["creators"] or ""
            out.append(Signal(
                source=self.name, kind="zotero_item", title=f"新文献：{title[:100]}",
                body=f"类型：{r['typeName']}\n作者：{creators}\n分类：{coll}\n加入时间：{added}",
                url=f"zotero://select/items/{r['itemID']}", actor=creators,
                occurred_at=added, project=coll, importance=0.45,
                raw={"itemID": r["itemID"], "title": title, "collections": coll},
            ))
        return out

    def _cutoff(self, days: int) -> str:
        from datetime import timedelta
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _iso(self, value: str) -> str:
        if not value:
            return ""
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
        except Exception:
            return ""
