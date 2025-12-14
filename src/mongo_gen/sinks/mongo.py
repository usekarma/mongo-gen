from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
from pymongo import MongoClient
from pymongo.collection import Collection

@dataclass
class MongoSink:
    uri: str
    db: str
    collection: str

    def connect(self) -> Collection:
        client = MongoClient(self.uri)
        return client[self.db][self.collection]

    def insert_one(self, coll: Collection, doc: Dict[str, Any]) -> None:
        coll.insert_one(doc)

    def update_one(self, coll: Collection, _id: str, updates: Dict[str, Any]) -> None:
        coll.update_one({"_id": _id}, {"$set": updates})

