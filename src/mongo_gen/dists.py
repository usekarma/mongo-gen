\
from __future__ import annotations
import math, random, re
from typing import Dict, Union

_DIST_RE = re.compile(r"^(?P<name>[a-zA-Z_]+)\((?P<args>.*)\)$")

def parse_ms(expr: str, rng: random.Random) -> int:
    expr = expr.strip()
    m = _DIST_RE.match(expr)
    if not m:
        raise ValueError(f"Bad duration distribution: {expr}")
    name = m.group("name")
    args = [a.strip() for a in m.group("args").split(",") if a.strip()]

    if name == "fixed":
        if len(args) != 1:
            raise ValueError("fixed(x) requires 1 arg")
        return int(float(args[0]))

    if name == "uniform":
        if len(args) != 2:
            raise ValueError("uniform(a,b) requires 2 args")
        a = float(args[0]); b = float(args[1])
        return int(rng.uniform(a, b))

    if name == "lognormal":
        if len(args) != 2:
            raise ValueError("lognormal(mean_ms,sigma) requires 2 args")
        mean_ms = float(args[0])
        sigma = float(args[1])
        mu = math.log(max(mean_ms, 1.0)) - (sigma * sigma) / 2.0
        return int(rng.lognormvariate(mu, sigma))

    raise ValueError(f"Unknown distribution: {name}")

def weighted_choice(values: Dict[str, Union[int, float]], rng: random.Random) -> str:
    items = list(values.items())
    total = sum(float(w) for _, w in items)
    if total <= 0:
        raise ValueError("weighted_choice total weight must be > 0")
    r = rng.random() * total
    upto = 0.0
    for k, w in items:
        upto += float(w)
        if r <= upto:
            return k
    return items[-1][0]
