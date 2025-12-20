from mongo_gen.scenario import load_scenario
from mongo_gen.engine import generate_runs

def test_deterministic_with_seed():
    s = load_scenario("examples/scenarios/brownout.yaml", seed_override=42)
    a = list(generate_runs(s, seed=42, include_event_time=False))
    b = list(generate_runs(s, seed=42, include_event_time=False))
    assert a == b
    assert len(a) > 0
