from mongo_gen.scenario import load_scenario
from mongo_gen.engine import generate_runs

def test_incident_present_in_window_only():
    s = load_scenario("examples/scenarios/brownout.yaml", seed_override=123)
    inc = s.incidents[0]
    inc_start = s.start_time + inc.at
    inc_end = inc_start + inc.duration

    seen_inc = 0
    seen_outside = 0

    for r in generate_runs(s, seed=123):
        in_win = inc_start <= r.requested_at < inc_end and r.report_type == (inc.scope_report_type or r.report_type)
        has_inc = (r.incident_id == inc.incident_id)

        if in_win and has_inc:
            seen_inc += 1
        if (not in_win) and has_inc:
            seen_outside += 1

    assert seen_inc > 0
    assert seen_outside == 0
