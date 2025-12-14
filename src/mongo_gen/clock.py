from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

UTC = timezone.utc

@dataclass
class Clock:
    mode: str = "realtime"   # realtime | accelerated
    speed: float = 60.0      # accelerated speed multiplier
    _sim_now: datetime | None = None

    def now(self) -> datetime:
        if self.mode == "accelerated":
            if self._sim_now is None:
                self._sim_now = datetime.now(UTC)
            return self._sim_now
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.mode == "accelerated":
            if self._sim_now is None:
                self._sim_now = datetime.now(UTC)
            self._sim_now = self._sim_now + timedelta(seconds=seconds * self.speed)
            return
        time.sleep(seconds)

