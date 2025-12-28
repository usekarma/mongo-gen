import json
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
