import argparse
import json
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


def _parse_start_time(s: str) -> datetime:
    # Accepts: 2025-01-01T00:00:00Z  (or with +00:00)
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv=None):
    p = argparse.ArgumentParser()
    g = p.add_subparsers(dest="cmd", required=True)

    # -------------------------
    # anchor
    # -------------------------
    a = g.add_parser("anchor", help="Print the effective UTC window (start/end) for a duration")
    a.add_argument("--duration", required=True)
    a.add_argument("--end-time", default="", help="UTC end time (ISO8601). Default: now (UTC)")
    a.add_argument("--format", choices=["json", "text"], default="json")

    def _run_anchor(args):
        dur = _dur(args.duration)
        end = _parse_start_time(args.end_time) if args.end_time else datetime.now(timezone.utc)
        start = end - dur
        if args.format == "text":
            print(f"start={_iso_z(start)} end={_iso_z(end)} duration={args.duration}")
        else:
            print(json.dumps({"start_time": _iso_z(start), "end_time": _iso_z(end), "duration": args.duration}))
        return 0

    a.set_defaults(func=_run_anchor)

    # -------------------------
    # generate
    # -------------------------
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
    c.add_argument("--seed", type=int, default=123)
    c.add_argument("--rps", type=float, default=2.0)

    c.add_argument(
        "--start-time",
        default="",
        help="UTC start time (ISO8601, e.g. 2025-01-01T00:00:00Z). Default: end-now window.",
    )
    c.add_argument(
        "--end-time",
        default="",
        help="UTC end time (ISO8601). Used only when --start-time is not provided. Default: now (UTC).",
    )

    def _run_generate(args):
        dur = _dur(args.duration)

        if args.start_time:
            start = _parse_start_time(args.start_time)
        else:
            end = _parse_start_time(args.end_time) if args.end_time else datetime.now(timezone.utc)
            start = end - dur

        s = Scenario(
            start_time=start,
            duration=dur,
            seed=args.seed,
            rps=args.rps,
            ids=args.ids,
        )
        ops = iter_ops(s)

        return emit(
            ops,
            mode=args.emit,
            out=args.out,
            mongo_uri=args.mongo_uri,
            mongo_db=args.mongo_db,
            mongo_coll=args.mongo_coll,
            batch_size=args.batch_size,
            unordered=args.unordered,
            drop=args.drop,
        )

    c.set_defaults(func=_run_generate)

    args = p.parse_args(argv)
    return args.func(args)
