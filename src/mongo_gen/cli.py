from __future__ import annotations
import argparse
import sys

from .scenario import load_scenario, validate_scenario
from .engine import generate_runs, run_to_mongo_ops
from .emit import write_jsonl
from .emit_mongo import MongoTarget, apply_ops

def _cmd_generate(args: argparse.Namespace) -> int:
    s = load_scenario(
        args.scenario,
        seed_override=args.seed,
        start_time_override=args.start_time,
        duration_override=args.duration,
    )
    errs = validate_scenario(s)
    if errs:
        for e in errs:
            print(f"[scenario error] {e}", file=sys.stderr)
        return 2

    # For realtime, we need to sort ops anyway; generating runs into memory is acceptable for PoC.
    runs_iter = list(generate_runs(s, seed=args.seed)) if (args.emit == "mongo" and args.mode == "realtime") else generate_runs(s, seed=args.seed)

    if args.emit == "jsonl":
        docs = (r.to_jsonable(include_event_time=(not args.no_event_time)) for r in runs_iter)
        n = write_jsonl(docs, out_path=args.out)
        if args.out:
            print(f"[mongo-gen] wrote {n} docs to {args.out}", file=sys.stderr)
        return 0

    def iter_ops():
        for r in runs_iter:
            ins, upd = run_to_mongo_ops(r, labels_on_insert=args.labels_on_insert)
            yield ins
            yield upd

    target = MongoTarget(uri=args.mongo_uri, db=args.mongo_db, coll=args.mongo_coll)

    ops = list(iter_ops()) if (args.sort_ops or args.mode == "realtime") else iter_ops()
    if isinstance(ops, list) and args.sort_ops:
        ops.sort(key=lambda o: o.when)

    n = apply_ops(
        ops,
        target=target,
        mode=args.mode,
        speed=args.speed,
        batch=args.batch,
        ordered=(not args.unordered),
    )
    print(f"[mongo-gen] mongo writes: {n}", file=sys.stderr)
    return 0

def _cmd_preview(args: argparse.Namespace) -> int:
    s = load_scenario(args.scenario, seed_override=args.seed, start_time_override=args.start_time, duration_override=args.duration)
    errs = validate_scenario(s)
    if errs:
        for e in errs:
            print(f"[scenario error] {e}", file=sys.stderr)
        return 2

    n = 0
    min_t = None
    max_t = None
    inc_counts = {}
    for r in generate_runs(s, seed=args.seed):
        n += 1
        t = r.requested_at
        min_t = t if min_t is None else min(min_t, t)
        max_t = t if max_t is None else max(max_t, t)
        inc = r.incident_id or "none"
        inc_counts[inc] = inc_counts.get(inc, 0) + 1

    print("scenario_id:", s.scenario_id)
    print("start_time:", s.start_time.isoformat().replace("+00:00","Z"))
    print("duration_s:", int(s.duration.total_seconds()))
    print("tick_s:", s.tick.total_seconds())
    print("seed:", s.seed if args.seed is None else args.seed)
    print("docs:", n)
    print("requested_at_min:", min_t.isoformat().replace("+00:00","Z") if min_t else None)
    print("requested_at_max:", max_t.isoformat().replace("+00:00","Z") if max_t else None)
    print("incident_counts:", inc_counts)
    return 0

def _cmd_scenario_lint(args: argparse.Namespace) -> int:
    s = load_scenario(args.scenario, seed_override=args.seed, start_time_override=args.start_time, duration_override=args.duration)
    errs = validate_scenario(s)
    if errs:
        for e in errs:
            print(f"[scenario error] {e}", file=sys.stderr)
        return 2
    print("[mongo-gen] scenario OK")
    return 0

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mongo-gen", description="Scenario-driven deterministic generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--scenario", required=True, help="Path to scenario YAML")
    common.add_argument("--seed", type=int, default=None, help="Override RNG seed")
    common.add_argument("--start-time", default=None, help="Override scenario start_time (ISO-8601, e.g. 2025-12-19T09:00:00Z)")
    common.add_argument("--duration", default=None, help="Override scenario duration (e.g. 3h, 30m)")

    g = sub.add_parser("generate", parents=[common], help="Generate events")
    g.add_argument("--emit", choices=["jsonl", "mongo"], default="jsonl", help="Output sink")
    g.add_argument("--out", default=None, help="Write JSONL to file (default stdout)")
    g.add_argument("--no-event-time", action="store_true", help="Do not include real wall-clock event_time in JSONL")
    g.add_argument("--mongo-uri", default="mongodb://localhost:27017", help="MongoDB URI")
    g.add_argument("--mongo-db", default="reports", help="MongoDB database")
    g.add_argument("--mongo-coll", default="report_runs", help="MongoDB collection")
    g.add_argument("--mode", choices=["backfill", "realtime"], default="backfill", help="Mongo apply mode")
    g.add_argument("--speed", type=float, default=1.0, help="Realtime speedup (e.g. 60 = 60x faster)")
    g.add_argument("--batch", type=int, default=1000, help="Bulk write batch size")
    g.add_argument("--unordered", action="store_true", help="Use unordered bulk writes")
    g.add_argument("--sort-ops", action="store_true", help="Sort ops by canonical time (memory-heavy)")
    g.add_argument("--labels-on-insert", action="store_true", help="Include incident_id/tags on INSERT (default: only on UPDATE)")
    g.set_defaults(func=_cmd_generate)

    pr = sub.add_parser("preview", parents=[common], help="Preview scenario output stats")
    pr.set_defaults(func=_cmd_preview)

    sc = sub.add_parser("scenario", help="Scenario utilities")
    sc_sub = sc.add_subparsers(dest="scmd", required=True)
    lint = sc_sub.add_parser("lint", parents=[common], help="Validate scenario YAML")
    lint.set_defaults(func=_cmd_scenario_lint)

    args = p.parse_args(argv)
    return int(args.func(args))

if __name__ == "__main__":
    raise SystemExit(main())
