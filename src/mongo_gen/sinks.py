from __future__ import annotations
import json, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

Event = Dict[str, Any]

class Sink:
    def emit(self, event: Event) -> None:  # pragma: no cover
        raise NotImplementedError
    def close(self) -> None:  # pragma: no cover
        return

@dataclass
class JsonlSink(Sink):
    path: Path
    _fp: Optional[Any] = None

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.path.open("a", encoding="utf-8")

    def emit(self, event: Event) -> None:
        assert self._fp is not None
        self._fp.write(json.dumps(event, default=str) + "\n")

    def close(self) -> None:
        if self._fp:
            self._fp.flush()
            self._fp.close()
            self._fp = None

class MultiSink(Sink):
    def __init__(self, sinks: list[Sink]) -> None:
        self.sinks = sinks
    def emit(self, event: Event) -> None:
        for s in self.sinks:
            s.emit(event)
    def close(self) -> None:
        for s in self.sinks:
            s.close()

class RateLimitSink(Sink):
    """Token-bucket rate limiter around another sink."""
    def __init__(self, sink: Sink, max_qps: float) -> None:
        self.sink = sink
        self.max_qps = float(max_qps)
        self.tokens = float(max_qps)
        self.last = time.monotonic()

    def emit(self, event: Event) -> None:
        if self.max_qps <= 0:
            self.sink.emit(event)
            return
        while True:
            now = time.monotonic()
            elapsed = now - self.last
            self.last = now
            self.tokens = min(self.max_qps, self.tokens + elapsed * self.max_qps)
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                self.sink.emit(event)
                return
            need = 1.0 - self.tokens
            time.sleep(max(0.0, need / self.max_qps))

    def close(self) -> None:
        self.sink.close()
