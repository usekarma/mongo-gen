import json
from datetime import datetime, timezone
from mongo_gen.cli import main


def test_anchor_json_output_is_parseable(capsys):
    rc = main(["anchor", "--duration", "10s"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    doc = json.loads(out)
    assert "start_time" in doc
    assert "end_time" in doc
    assert doc["duration"] == "10s"


def test_anchor_respects_end_time(capsys):
    rc = main(["anchor", "--duration", "10s", "--end-time", "2025-01-01T00:00:10Z"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["end_time"] == "2025-01-01T00:00:10Z"
    assert doc["start_time"] == "2025-01-01T00:00:00Z"


def _parse_z(s: str) -> datetime:
    # "2025-01-01T00:00:00Z" -> aware UTC datetime
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def test_anchor_json_has_start_end_and_duration(capsys):
    # Use a fixed end-time so the test is deterministic
    rc = main(["anchor", "--duration", "10m", "--end-time", "2025-01-01T00:10:00Z", "--format", "json"])
    assert rc == 0

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)

    assert payload["duration"] == "10m"
    assert "start_time" in payload
    assert "end_time" in payload

    start = _parse_z(payload["start_time"])
    end = _parse_z(payload["end_time"])

    assert end - start == (end - end.replace(minute=end.minute - 10))  # sanity shape
    assert int((end - start).total_seconds()) == 600