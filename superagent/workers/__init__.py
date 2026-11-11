from .claude import ClaudeWorker
from .codex import CodexWorker
from .deeptutor import DeepTutorWorker
from .local import LocalWorker

WORKERS = {w.name: w for w in (ClaudeWorker, CodexWorker, DeepTutorWorker, LocalWorker)}


def get_worker(name: str):
    return WORKERS.get(name)
