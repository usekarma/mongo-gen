from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from .clock import Clock

class Scenario(Protocol):
    name: str
    def tick(self) -> None: ...

@dataclass
class Engine:
    scenario: Scenario
    clock: Clock
    duration_seconds: int
    emit_every_seconds: float

    def run(self) -> None:
        start = self.clock.now()
        end = start + timedelta(seconds=self.duration_seconds)

        next_emit = start
        while self.clock.now() < end:
            now = self.clock.now()
            if now >= next_emit:
                self.scenario.tick()
                next_emit = next_emit + timedelta(seconds=self.emit_every_seconds)
            # small sleep to avoid tight loop
            self.clock.sleep(0.05)

