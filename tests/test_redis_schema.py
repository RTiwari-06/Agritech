"""Redis data-layer schema tests (spec §3.2–§3.4) against a fake client.

These exercise the payload/key layout without a live server: per-stream chat
Lists (``stream:<id>:chats``), the spec-shaped chat payload aliases, and the
default-stream seeding contract.
"""

from __future__ import annotations

import fnmatch
import json

import pytest

from database import redis_db


class FakeRedis:
    """Minimal in-memory redis.Redis clone for the schema tests."""

    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.values: dict[str, str] = {}

    # -- List API ----------------------------------------------------------
    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def ltrim(self, key, start, end):
        if key in self.lists:
            self.lists[key] = self.lists[key][start : end + 1]
        return True

    def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        if end < 0:
            end = len(values) + end + 1
        return values[start:end]

    # -- Hash API ------------------------------------------------------------
    def hset(self, key, mapping=None, **kwargs):
        self.hashes.setdefault(key, {}).update(
            {name: str(value) for name, value in (mapping or {}).items()}
        )
        return len(mapping or {})

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def exists(self, key):
        return int(
            key in self.values or key in self.hashes or key in self.lists or key in self.sets
        )

    def set(self, key, value):
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    # -- Set API ------------------------------------------------------------
    def sadd(self, key, *members):
        members = [str(member) if not isinstance(member, str) else member for member in members]
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    # -- Scan ---------------------------------------------------------------
    def scan_iter(self, match=None):
        keys = [*self.lists, *self.hashes, *self.sets]
        return [
            key
            for key in keys
            if not match or fnmatch.fnmatchcase(key, match)
        ]


@pytest.fixture()
def fake_client(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(redis_db, "get_client", lambda: client)
    return client


def test_chat_key_layout_matches_spec():
    assert redis_db.chat_key("1") == "stream:1:chats"
    assert redis_db.DEFAULT_STREAM_ID == "1"
    assert redis_db.DEFAULT_STREAM["title"] == "Bangalore APMC Live Auction"


def test_push_chat_message_writes_spec_shaped_payload_to_stream_lists(fake_client):
    redis_db.push_chat_message(
        {
            "stream_id": "1",
            "message_text": "The onions smell fresh",
            "sentiment_score": 0.62,
            "product_id": "1",
            "intent_tag": "QUALITY_INQUIRY",
        }
    )

    assert "stream:1:chats" in fake_client.lists
    payload = json.loads(fake_client.lists["stream:1:chats"][0])
    assert payload["message"] == "The onions smell fresh"
    assert payload["message_text"] == "The onions smell fresh"
    assert payload["sentiment"] == 0.62
    assert payload["sentiment_score"] == 0.62
    assert isinstance(payload["id"], str) and payload["id"]
    assert isinstance(payload["timestamp"], int)
    assert payload["created_at"]
    assert payload["stream_id"] == "1"


def test_push_chat_message_defaults_to_stream_one(fake_client):
    redis_db.push_chat_message({"message_text": "Any discounts today?"})

    keys = fake_client.scan_iter(match="stream:*:chats")
    assert keys == ["stream:1:chats"]


def test_get_recent_chats_merges_streams_newest_first(fake_client):
    older = {
        "message": "older note",
        "sentiment": 0.1,
        "created_at": "2026-10-27T10:00:00+00:00",
        "stream_id": "1",
    }
    newer = {
        "message": "newer note",
        "sentiment": 0.3,
        "timestamp": 1_800_000_000,  # epoch far above the ISO notes below
        "stream_id": "2",
    }
    legacy = {
        "message_text": "legacy note",
        "sentiment_score": -0.2,
        "created_at": "2026-10-01T10:00:00+00:00",
        "stream_id": "active",
    }
    # Push in the "wrong" order (legacy already normalized by the helper).
    redis_db.push_chat_message(older)
    redis_db.push_chat_message(newer)
    fake_client.lists[redis_db.LEGACY_CHAT_KEY] = [
        json.dumps(legacy),
        "not-json",  # malformed rows are skipped
    ]

    chats = redis_db.get_recent_chats(limit=3)

    texts = [
        chat.get("message") or chat.get("message_text") for chat in chats
    ]
    assert texts[0] == "newer note"            # epoch timestamp wins
    assert texts[1] == "older note"            # ISO timestamp converted
    assert texts[2] == "legacy note"           # pre-refactor key still visible


def test_seed_default_stream_is_idempotent(fake_client):
    redis_db.seed_default_stream(fake_client)

    assert fake_client.smembers("streams:all") == {"1"}
    first = fake_client.hgetall("stream:1")
    assert first == {
        "id": "1",
        "title": "Bangalore APMC Live Auction",
        "status": "active",
    }

    # A seller-updated title must survive re-seeding.
    fake_client.hset("stream:1", mapping={"title": "Seller Auction"})
    redis_db.seed_default_stream(fake_client)
    assert fake_client.hgetall("stream:1")["title"] == "Seller Auction"


def test_market_report_rows_are_idempotent_and_filterable(fake_client):
    rows = [
        {
            "commodity": "Tomato",
            "market": "Bengaluru",
            "market_date": "2026-10-25",
            "price_per_kg": 20.0,
            "msp_per_kg": None,
            "arrival_metric_tonnes": 12.0,
        },
        {
            "commodity": "Tomato",
            "market": "Bengaluru",
            "market_date": "2026-10-26",
            "price_per_kg": 22.0,
            "msp_per_kg": 15.0,
            "arrival_metric_tonnes": None,
        },
    ]

    assert redis_db.store_market_records(rows) == {"inserted": 2, "updated": 0}
    assert redis_db.store_market_records([rows[1]]) == {"inserted": 0, "updated": 1}
    assert redis_db.get_market_records(commodity="tomato", market="BENGALURU") == [
        rows[1],
        rows[0],
    ]
    assert redis_db.get_market_records(limit=1) == [rows[1]]