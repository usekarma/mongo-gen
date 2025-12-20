from __future__ import annotations
import argparse
import sys
from .scenario import load_scenario, validate_scenario
from .engine import generate_runs
from .emit import write_jsonl

def _cmd_generate(args: argparse.Namespace) -> int:
    s = load_scenario(args.scenario, seed_override=args.seed, start_time_override=args.start_time, duration_override=args.duration)
    errs = validate_scenario(s)
    if errs:
        for e in errs:
            print(f"[scenario error] {e}", file=sys.stderr)
        return 2
    docs = generate_runs(s, seed=args.seed, include_event_time=(not args.no_event_time))
    n = write_jsonl(docs, out_path=args.out)
    if args.out:
        print(f"[mongo-gen] wrote {n} docs to {args.out}", file=sys.stderr)
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
    for d in generate_runs(s, seed=args.seed, include_event_time=False):
        n += 1
        t = d["requested_at"]
        min_t = t if min_t is None else min(min_t, t)
        max_t = t if max_t is None else max(max_t, t)
        inc = d.get("incident_id") or "none"
        inc_counts[inc] = inc_counts.get(inc, 0) + 1

    print("scenario_id:", s.scenario_id)
    print("start_time:", s.start_time.isoformat().replace("+00:00","Z"))
    print("duration_s:", int(s.duration.total_seconds()))
    print("tick_s:", s.tick.total_seconds())
    print("seed:", s.seed if args.seed is None else args.seed)
    print("docs:", n)
    print("requested_at_min:", min_t)
    print("requested_at_max:", max_t)
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
    p = argparse.ArgumentParser(prog="mongo-gen", description="Scenario-driven deterministic event generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--scenario", required=True, help="Path to scenario YAML")
    common.add_argument("--seed", type=int, default=None, help="Override RNG seed")
    common.add_argument("--start-time", default=None, help="Override scenario start_time (ISO-8601, e.g. 2025-12-19T09:00:00Z)")
    common.add_argument("--duration", default=None, help="Override scenario duration (e.g. 3h, 30m)")

    g = sub.add_parser("generate", parents=[common], help="Generate JSONL events")
    g.add_argument("--out", default=None, help="Write JSONL to file (default stdout)")
    g.add_argument("--no-event-time", action="store_true", help="Do not include real wall-clock event_time")
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
