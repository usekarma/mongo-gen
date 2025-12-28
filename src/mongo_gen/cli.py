import argparse
from datetime import datetime, timedelta, timezone
from .engine import Scenario, iter_ops
from .emit import emit_jsonl

def _dur(s):
    return timedelta(seconds=int(s[:-1]))

def main(argv=None):
    p=argparse.ArgumentParser()
    g=p.add_subparsers(dest="cmd",required=True)
    c=g.add_parser("generate")
    c.add_argument("--duration",required=True)
    c.add_argument("--out",default="-")
    c.set_defaults(func=lambda a: emit_jsonl(iter_ops(Scenario(datetime.now(timezone.utc)-_dur(a.duration),_dur(a.duration))),a.out))
    a=p.parse_args(argv)
    return a.func(a)
