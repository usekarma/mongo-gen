from mongo_gen.scenario import load_scenario
from mongo_gen.engine import generate_runs

def test_deterministic_with_seed():
    s = load_scenario("examples/scenarios/brownout.yaml", seed_override=42)
    a = [r.to_jsonable(include_event_time=False) for r in generate_runs(s, seed=42)]
    b = [r.to_jsonable(include_event_time=False) for r in generate_runs(s, seed=42)]
    assert a == b
    assert len(a) > 0
