from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Sequence, Tuple

@dataclass
class RNG:
    seed: int

    def __post_init__(self) -> None:
        self._r = random.Random(self.seed)

    def randint(self, a: int, b: int) -> int:
        return self._r.randint(a, b)

    def random(self) -> float:
        return self._r.random()

    def choice(self, seq: Sequence[Any]) -> Any:
        return self._r.choice(seq)

    def weighted_choice(self, items: Sequence[Tuple[Any, float]]) -> Any:
        # items is [(value, weight), ...]
        total = sum(w for _, w in items)
        if total <= 0:
            return self.choice([v for v, _ in items])
        x = self._r.random() * total
        upto = 0.0
        for v, w in items:
            upto += w
            if upto >= x:
                return v
        return items[-1][0]

