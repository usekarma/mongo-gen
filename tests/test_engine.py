from datetime import datetime, timedelta, timezone
from mongo_gen.engine import Scenario, iter_ops

def test_basic():
    s=Scenario(datetime(2025,1,1,tzinfo=timezone.utc),timedelta(seconds=2))
    ops=list(iter_ops(s))
    assert len(ops)>0
