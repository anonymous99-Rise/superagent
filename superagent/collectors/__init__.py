from .filesystem import FilesystemCollector
from .git import GitCollector
from .github import GitHubCollector
from .zotero import ZoteroCollector
from .dsh import DshCollector
from .obsidian import ObsidianCollector
from .calendar import CalendarCollector
from .email import EmailCollector

ALL = [
    FilesystemCollector,
    GitCollector,
    GitHubCollector,
    ZoteroCollector,
    DshCollector,
    ObsidianCollector,
    CalendarCollector,
    EmailCollector,
]


def build_collectors(cfg, store):
    out = []
    for cls in ALL:
        c = cls(cfg, store)
        ok, reason = c.available()
        if ok:
            out.append(c)
        else:
            c.unavailable_reason = reason
    return out
