from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from mongo_gen.emit import emit_mongo
from mongo_gen.engine import Op


def test_emit_mongo_drop_calls_drop_once():
    ops = [
        Op(
            when=datetime.now(timezone.utc),
            kind="insert",
            run_id="run-00000001",
            payload={"_id": "run-00000001"},
        ),
        Op(
            when=datetime.now(timezone.utc),
            kind="update",
            run_id="run-00000001",
            payload={"$set": {"status": "SUCCESS"}},
        ),
    ]

    mock_collection = MagicMock()
    mock_client = MagicMock()

    # client[db][coll] chaining:
    mock_client.__getitem__.return_value.__getitem__.return_value = mock_collection

    # Fake write models so emit_mongo can append them
    fake_insert = lambda doc: ("InsertOne", doc)
    fake_update = lambda flt, upd, upsert=False: ("UpdateOne", flt, upd, upsert)

    with patch("pymongo.MongoClient", return_value=mock_client), \
         patch("pymongo.InsertOne", side_effect=fake_insert), \
         patch("pymongo.UpdateOne", side_effect=fake_update):
        rc = emit_mongo(
            ops,
            mongo_uri="mongodb://example",
            mongo_db="reports",
            mongo_coll="report_runs",
            drop=True,
        )

    assert rc == 0
    mock_collection.drop.assert_called_once()
    mock_collection.bulk_write.assert_called()
