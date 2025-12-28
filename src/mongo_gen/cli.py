import argparse
from datetime import datetime, timedelta, timezone

from .engine import Scenario, iter_ops
from .emit import emit


def _dur(s: str) -> timedelta:
    # supports 10s, 5m, 2h
    unit = s[-1]
    n = int(s[:-1])
    if unit == "s":
        return timedelta(seconds=n)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    raise ValueError(f"Bad duration {s!r} (use Ns/Nm/Nh)")


def main(argv=None):
    p = argparse.ArgumentParser()
    g = p.add_subparsers(dest="cmd", required=True)

    c = g.add_parser("generate")
    c.add_argument("--duration", required=True)
    c.add_argument("--out", default="-")

    c.add_argument("--emit", choices=["jsonl", "mongo"], default="jsonl")
    c.add_argument("--mongo-uri", default="")
    c.add_argument("--mongo-db", default="")
    c.add_argument("--mongo-coll", default="report_runs")
    c.add_argument("--batch-size", type=int, default=1000)
    c.add_argument("--unordered", action="store_true")
    c.add_argument("--drop", action="store_true", help="Drop the Mongo collection before writing")
    c.add_argument("--ids", choices=["deterministic", "random"], default="deterministic")

    def _run(a):
        dur = _dur(a.duration)
        start = datetime.now(timezone.utc) - dur
        ops = iter_ops(Scenario(start, dur, ids=a.ids))
        return emit(
            ops,
            mode=a.emit,
            out=a.out,
            mongo_uri=a.mongo_uri,
            mongo_db=a.mongo_db,
            mongo_coll=a.mongo_coll,
            batch_size=a.batch_size,
            unordered=a.unordered,
            drop=a.drop,
        )

    c.set_defaults(func=_run)

    a = p.parse_args(argv)
    return a.func(a)
