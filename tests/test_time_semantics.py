from mongo_gen.scenario import load_scenario
from mongo_gen.engine import generate_runs
from mongo_gen.utils import parse_iso_utc

def test_requested_at_within_bounds():
    s = load_scenario("examples/scenarios/brownout.yaml", seed_override=7)
    start = s.start_time
    end = s.start_time + s.duration
    for d in generate_runs(s, seed=7, include_event_time=False):
        ra = parse_iso_utc(d["requested_at"])
        ca = parse_iso_utc(d["completed_at"])
        assert start <= ra <= end
        assert ca >= ra
        assert d["latency_ms"] >= 1
