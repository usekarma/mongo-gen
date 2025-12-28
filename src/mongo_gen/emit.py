import json, sys
from .engine import Op

def emit_jsonl(ops, out):
    f = sys.stdout if out == "-" else open(out,"w")
    for op in ops:
        f.write(json.dumps({"when":op.when.isoformat(),"kind":op.kind,"run_id":op.run_id,"payload":op.payload})+"\n")
    if f is not sys.stdout:
        f.close()
