from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pymongo import MongoClient
from pymongo.collection import Collection

Event = Dict[str, Any]

# Fields that should be written once and never overwritten on subsequent updates.
IMMUTABLE_FIELDS = {
    "run_id",
    "subscriber_id",
    "report_type",
    "requested_at",
    "created_at",
    "attempt",
}


def _parse_iso_z(s: Any) -> Optional[datetime]:
    """
    Parse ISO8601 timestamps like:
      2025-12-17T22:00:00Z
      2025-12-17T22:00:00+00:00
    into timezone-aware UTC datetimes.
    """
    if not isinstance(s, str):
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _to_mongo_dates(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert common timestamp fields that are ISO strings into Python datetime
    so Mongo stores them as BSON Date.
    """
    out = dict(doc)
    for k in list(out.keys()):
        if k.endswith("_at") or k in ("event_time", "last_event_time", "created_at", "updated_at"):
            dt = _parse_iso_z(out.get(k))
            if dt is not None:
                out[k] = dt
    return out


@dataclass
class MongoStateSink:
    """
    Writes "current state" docs keyed by `key_field` (default run_id).

    - Insert once (upsert) with immutable fields via $setOnInsert
    - Update mutable fields on every event via $set
    - Optional compact per-run history via $push history
    """
    uri: str
    database: str
    collection: str
    key_field: str = "run_id"
    history: bool = False

    client: Optional[MongoClient] = None
    coll: Optional[Collection] = None

    def __post_init__(self) -> None:
        self.client = MongoClient(self.uri)
        self.coll = self.client[self.database][self.collection]
        self.coll.create_index(self.key_field, unique=True)

    def emit(self, event: Event) -> None:
        assert self.coll is not None

        key = event.get(self.key_field)
        if not key:
            return

        ev = _to_mongo_dates(event)

        # Split fields into $setOnInsert (immutable) vs $set (mutable)
        set_on_insert: Dict[str, Any] = {}
        set_fields: Dict[str, Any] = {}

        for k, v in ev.items():
            if k in IMMUTABLE_FIELDS:
                set_on_insert[k] = v
            else:
                set_fields[k] = v

        # Convenience: always keep pointers to the last event
        # (these are mutable, so keep in $set)
        set_fields["last_event_time"] = ev.get("event_time")
        set_fields["last_event"] = ev.get("event")

        update: Dict[str, Any] = {"$set": set_fields, "$setOnInsert": set_on_insert}

        if self.history:
            update["$push"] = {
                "history": {
                    "t": ev.get("event_time"),
                    "stage": ev.get("stage"),
                    "event": ev.get("event"),
                    "status": ev.get("status"),
                }
            }

        self.coll.update_one({self.key_field: key}, update, upsert=True)

    def close(self) -> None:
        if self.client:
            self.client.close()
            self.client = None
            self.coll = None
