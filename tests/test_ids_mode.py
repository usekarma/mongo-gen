from datetime import datetime, timedelta, timezone

from mongo_gen.engine import Scenario, iter_ops


def _first_run_id(s: Scenario) -> str:
    for op in iter_ops(s):
        if op.kind == "insert":
            return op.run_id
    raise AssertionError("no insert ops produced")


def test_ids_deterministic_is_stable():
    s = Scenario(
        start_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        duration=timedelta(seconds=2),
        seed=1,
        rps=1.0,
        ids="deterministic",
    )
    a = _first_run_id(s)
    b = _first_run_id(s)
    assert a == b


def test_ids_random_changes_between_runs():
    s = Scenario(
        start_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        duration=timedelta(seconds=2),
        seed=1,
        rps=1.0,
        ids="random",
    )
    a = _first_run_id(s)
    b = _first_run_id(s)
    assert a != b
