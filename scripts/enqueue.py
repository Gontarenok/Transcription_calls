from __future__ import annotations

import argparse
from datetime import datetime

from jobs.classify_calls import classify_enqueue_pending
from jobs.summarize_911 import summarize_enqueue_pending
from jobs.transcribe_911 import transcribe_911_run
from jobs.transcribe_kc import transcribe_kc_day


def _today_ddmmyyyy() -> str:
    return datetime.now().strftime("%d%m%Y")


def main() -> int:
    p = argparse.ArgumentParser(description="Enqueue background jobs into Celery/Redis.")
    sub = p.add_subparsers(dest="cmd", required=True)

    kc = sub.add_parser("kc_day")
    kc.add_argument("--day", default=None, help="DDMMYYYY; default=today")
    kc.add_argument("--model", default="medium")
    kc.add_argument("--root", default=None)
    kc.add_argument("--limit", type=int, default=100000)

    n911 = sub.add_parser("n911_run")
    n911.add_argument("--model", default="medium")
    n911.add_argument("--root", default=None)
    n911.add_argument("--recursive", action="store_true")
    n911.add_argument("--limit", type=int, default=100000)

    c = sub.add_parser("classify")
    c.add_argument("--limit", type=int, default=200)

    s = sub.add_parser("summarize_911")
    s.add_argument("--limit", type=int, default=100)

    args = p.parse_args()

    if args.cmd == "kc_day":
        day = args.day or _today_ddmmyyyy()
        res = transcribe_kc_day.delay(day=day, root=args.root, model=args.model, limit=args.limit)
        print(res.id)
        return 0
    if args.cmd == "n911_run":
        res = transcribe_911_run.delay(root=args.root, model=args.model, recursive=args.recursive, limit=args.limit)
        print(res.id)
        return 0
    if args.cmd == "classify":
        res = classify_enqueue_pending.delay(limit=args.limit)
        print(res.id)
        return 0
    if args.cmd == "summarize_911":
        res = summarize_enqueue_pending.delay(limit=args.limit)
        print(res.id)
        return 0

    raise SystemExit("Unknown command")


if __name__ == "__main__":
    raise SystemExit(main())

