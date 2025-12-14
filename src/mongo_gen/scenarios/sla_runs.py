from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import uuid

from ..clock import Clock
from ..rng import RNG
from ..sinks.mongo import MongoSink


@dataclass
class SLAConfig:
    subscribers: int
    report_types: dict  # {name: {sla_seconds:int, weight:float}}
    failure_rate: float
    breach_rate: float
    queue_delay_seconds: tuple[int, int]
    start_to_complete_factor_ok: tuple[float, float]
    start_to_complete_factor_breach: tuple[float, float]


@dataclass
class SLARunsScenario:
    name: str
    cfg: SLAConfig
    rng: RNG
    clock: Clock
    sink: MongoSink
    tag: str
    generator_id: str = "gen-0"   # ✅ new

    def __post_init__(self) -> None:
        self._coll = self.sink.connect()
        self._subscribers = [f"sub-{i:03d}" for i in range(1, self.cfg.subscribers + 1)]
        self._report_type_items = [
            (rt, float(meta.get("weight", 1.0)))
            for rt, meta in self.cfg.report_types.items()
        ]

    def tick(self) -> None:
        now = self.clock.now()

        # ✅ include generator_id for uniqueness & debugging
        run_id = f"run-{self.generator_id}-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

        subscriber_id = self.rng.choice(self._subscribers)
        report_type = self.rng.weighted_choice(self._report_type_items)
        sla_seconds = int(self.cfg.report_types[report_type]["sla_seconds"])

        is_failure = self.rng.random() < self.cfg.failure_rate
        is_breach = self.rng.random() < self.cfg.breach_rate

        qmin, qmax = self.cfg.queue_delay_seconds
        queue_delay = self.rng.randint(qmin, qmax)

        requested_at = now
        started_at = requested_at + timedelta(seconds=queue_delay)

        fmin, fmax = (
            self.cfg.start_to_complete_factor_breach if is_breach else self.cfg.start_to_complete_factor_ok
        )
        factor = fmin + (fmax - fmin) * self.rng.random()
        duration_s = max(1, int(sla_seconds * factor))
        completed_at = started_at + timedelta(seconds=duration_s)

        # 1) requested
        self._coll.insert_one({
            "_id": run_id,
            "run_id": run_id,
            "generator_id": self.generator_id,  # ✅ new
            "subscriber_id": subscriber_id,
            "report_type": report_type,
            "status": "requested",
            "error_code": "",
            "error_message": "",
            "requested_at": requested_at,
            "gen_tag": self.tag
        })

        # 2) running
        self.clock.sleep((started_at - requested_at).total_seconds())
        self._coll.update_one({"_id": run_id}, {"$set": {
            "status": "running",
            "started_at": started_at
        }})

        # 3) completed/failed
        self.clock.sleep((completed_at - started_at).total_seconds())
        if is_failure:
            self._coll.update_one({"_id": run_id}, {"$set": {
                "status": "failed",
                "error_code": "E_TIMEOUT",
                "error_message": "Upstream service timeout",
                "completed_at": completed_at
            }})
        else:
            self._coll.update_one({"_id": run_id}, {"$set": {
                "status": "completed",
                "completed_at": completed_at
            }})
