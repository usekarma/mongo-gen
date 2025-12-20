from mongo_gen.scenario import load_scenario
from mongo_gen.engine import generate_runs

def test_requested_at_within_bounds():
    s = load_scenario("examples/scenarios/brownout.yaml", seed_override=7)
    start = s.start_time
    end = s.start_time + s.duration
    for r in generate_runs(s, seed=7):
        assert start <= r.requested_at <= end
        assert r.completed_at >= r.requested_at
        assert r.latency_ms >= 1
