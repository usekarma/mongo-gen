from __future__ import annotations
from typing import Iterable, Optional
import json
import sys

def write_jsonl(docs: Iterable[dict], out_path: Optional[str] = None) -> int:
    if out_path:
        f = open(out_path, "w", encoding="utf-8")
        close = True
    else:
        f = sys.stdout
        close = False
    n = 0
    try:
        for d in docs:
            f.write(json.dumps(d, separators=(",", ":"), ensure_ascii=False))
            f.write("\n")
            n += 1
    finally:
        if close:
            f.close()
    return n
