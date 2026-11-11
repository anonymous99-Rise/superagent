from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

from . import __version__, util
from .config import Config, EXAMPLE_TOML, ROOT
from .engine import Engine


def _engine(cfg) -> Engine:
    return Engine(cfg)


def cmd_init(args) -> int:
    cfg = Config()
    if not cfg.path.exists():
        cfg.path.parent.mkdir(parents=True, exist_ok=True)
        cfg.path.write_text(EXAMPLE_TOML, encoding="utf-8")
        print(f"created {cfg.path}")
    e = _engine(cfg)
    print(f"database ready: {cfg.db_path()}")
    print(f"context entries: {len(e.store.context())}")
    return 0


def cmd_patrol(args) -> int:
    e = _engine(Config())
    stats = e.patrol_once(only=args.only or None)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def cmd_serve(args) -> int:
    from .api import serve

    cfg = Config()
    e = _engine(cfg)
    e.start_background()
    httpd = serve(e, host=args.host, port=args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        e.shutdown()
    return 0


def cmd_status(args) -> int:
    e = _engine(Config())
    data = {
        "overview": e.store.overview(),
        "verdict": "alive",
        "last_patrol": e.last_patrol,
        "workers": {},
        "collectors": e.store.all_collector_states(),
        "cost_today": e.store.today_cost(),
    }
    from .workers import WORKERS

    for name, cls in WORKERS.items():
        ok, reason = cls(e.cfg).available()
        data["workers"][name] = {"available": ok, "reason": reason}
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_items(args) -> int:
    e = _engine(Config())
    for it in e.store.items(status=args.status, limit=args.limit):
        print(f"{it['id']}  P{it['priority']} L{it['autonomy_level']} [{it['status']}] "
              f"{it['category']:<12} {it['title'][:80]}")
    return 0


def cmd_plan(args) -> int:
    e = _engine(Config())
    ids = e.plan_item(args.item_id, use_llm=args.llm)
    print(json.dumps({"task_ids": ids}, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args) -> int:
    e = _engine(Config())
    ids = e.run_item(args.item_id, use_llm=args.llm)
    print("queued:", ", ".join(ids))
    if args.wait:
        deadline = time.time() + args.wait
        while time.time() < deadline:
            e.dispatcher.tick()
            counts = e.store.task_counts()
            if not counts.get("pending") and not counts.get("running") and not e.dispatcher.active_count():
                break
            time.sleep(5)
        for t in e.store.tasks(item_id=args.item_id):
            print(f"[{t['status']}] {t['title']}")
    return 0


def cmd_approve(args) -> int:
    e = _engine(Config())
    e.hitl.decide(args.approval_id, "approved", args.note or "")
    print("approved")
    return 0


def cmd_reject(args) -> int:
    e = _engine(Config())
    e.hitl.decide(args.approval_id, "rejected", args.note or "")
    print("rejected")
    return 0


def cmd_add(args) -> int:
    e = _engine(Config())
    from . import autonomy

    a = autonomy.assess("todo", args.title, args.summary or "", 0.7 if args.priority <= 2 else 0.5)
    iid = e.store.create_item(
        title=args.title, summary=args.summary or "", category=args.category or a.category,
        priority=args.priority or a.priority, autonomy_level=args.level or a.level,
        project=args.project or "", why_now="手动添加", recommendation="待规划",
        meta={"source": "manual", "kind": "manual", "side_effect": a.side_effect},
    )
    e.store.log("info", "item.manual", args.title, actor="user", entity_type="item", entity_id=iid)
    print(iid)
    return 0


def cmd_digest(args) -> int:
    e = _engine(Config())
    res = e.digest.build(force=args.force, notify=not args.no_notify)
    slim = {k: v for k, v in res.items() if k != "markdown"}
    print(json.dumps(slim, ensure_ascii=False, indent=2))
    if res.get("markdown") and not args.show:
        print(f"\nmarkdown: {res.get('path')}")
    if args.show and res.get("markdown"):
        print(res["markdown"])
    return 0


def cmd_doctor(args) -> int:
    cfg = Config()
    e = _engine(cfg)
    problems = []
    print(f"SuperAgent v{__version__}")
    print(f"config: {cfg.path} ({'ok' if cfg.path.exists() else 'MISSING'})")
    print(f"db: {cfg.db_path()}")
    from .workers import WORKERS

    for name, cls in WORKERS.items():
        ok, reason = cls(cfg).available()
        print(f"worker {name:<7} {'OK' if ok else 'NO'}  {reason}")
        if name != "local" and not ok:
            problems.append(f"worker {name}: {reason}")
    dt = WORKERS["deeptutor"](cfg)
    if dt.available()[0]:
        dok, ddetail = dt.doctor()
        print(f"deeptutor doctor {'OK' if dok else 'NO'}  {ddetail}")
        if not dok:
            problems.append(f"deeptutor doctor: {ddetail}")
        cok, cdetail = dt.client_available()
        print(f"deeptutor client {'OK' if cok else 'NO'}  {cdetail}")
    from .collectors import ALL

    for cls in ALL:
        c = cls(cfg, e.store)
        ok, reason = c.available()
        print(f"collector {c.name:<11} {'OK' if ok else 'NO'}  {reason}")
    from .notify import Notifier

    n = Notifier(e.store, cfg)
    ok = n._toast("SuperAgent 自检", "通知通道工作正常") if n.enabled() else False
    print(f"notify desktop {'OK' if ok else 'NO'}")
    print(f"digest {'ON' if cfg.get('digest.enabled', True) else 'OFF'} "
          f"at {cfg.get('digest.time', '08:00')} | sources {', '.join(cfg.get('digest.sources') or [])}")
    print(f"context entries: {len(e.store.context())}")
    print("problems:", problems or "none")
    return 1 if problems else 0


def cmd_export(args) -> int:
    e = _engine(Config())
    data = {
        "exported_at": util.utcnow(),
        "items": e.store.items(limit=100000),
        "tasks": e.store.tasks(limit=100000),
        "approvals": e.store.approvals(limit=100000),
        "artifacts": e.store.artifacts(limit=100000),
        "context": e.store.context(),
        "events": e.store.events(limit=10000),
    }
    out = Path(args.file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported to {out}")
    return 0


def cmd_daemon(args) -> int:
    from .api import serve

    cfg = Config()
    e = _engine(cfg)
    e.start_background()
    httpd = serve(e, host=args.host, port=args.port)
    stopping = {"v": False}

    def stop(signum=None, frame=None):
        if stopping["v"]:
            return
        stopping["v"] = True
        e.store.log("info", "daemon.stop_signal", f"signal {signum}")
        e.shutdown()
        httpd.shutdown()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    e.store.log("info", "daemon.started", f"pid={__import__('os').getpid()}")
    try:
        httpd.serve_forever()
    finally:
        e.shutdown()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="superagent", description="Proactive mission control for local agents")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="initialize config/db/context").set_defaults(func=cmd_init)

    sp = sub.add_parser("patrol", help="run collectors + triage now")
    sp.add_argument("--only", nargs="*", help="collector names")
    sp.set_defaults(func=cmd_patrol)

    sp = sub.add_parser("serve", help="run dashboard + background engine")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("daemon", help="same as serve, with signal handling")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.set_defaults(func=cmd_daemon)

    sub.add_parser("status", help="print JSON status").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="self-check environment").set_defaults(func=cmd_doctor)

    sp = sub.add_parser("digest", help="generate the daily paper/skill/repo digest")
    sp.add_argument("--force", action="store_true", help="regenerate even if today's digest exists")
    sp.add_argument("--no-notify", action="store_true", help="skip the desktop notification")
    sp.add_argument("--show", action="store_true", help="print the markdown to stdout")
    sp.set_defaults(func=cmd_digest)

    sp = sub.add_parser("items", help="list inbox items")
    sp.add_argument("--status", default="")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_items)

    sp = sub.add_parser("plan", help="plan an item into subtasks")
    sp.add_argument("item_id")
    sp.add_argument("--llm", action="store_true")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("run", help="plan (if needed) and execute an item")
    sp.add_argument("item_id")
    sp.add_argument("--llm", action="store_true")
    sp.add_argument("--wait", type=int, default=0, help="seconds to wait for completion")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("approve", help="approve an outbox item")
    sp.add_argument("approval_id")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_approve)

    sp = sub.add_parser("reject", help="reject an outbox item")
    sp.add_argument("approval_id")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_reject)

    sp = sub.add_parser("add", help="manually add an inbox item")
    sp.add_argument("title")
    sp.add_argument("--summary", default="")
    sp.add_argument("--category", default="")
    sp.add_argument("--priority", type=int, default=0)
    sp.add_argument("--level", type=int, default=0)
    sp.add_argument("--project", default="")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("export", help="export a JSON backup")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_export)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
