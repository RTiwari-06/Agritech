"""SocketIO server logic: live marketplace feed, chat, and streaming.

All database access goes through ``session_scope()`` which yields a
:class:`MongoSession` whose ``session['collection']`` gives a pymongo
collection.  Documents use integer ``_id`` values.
"""

from flask_socketio import emit, join_room, leave_room

from database.db import session_scope
from database.models import (
    SentimentLabel,
    IntentTag,
    make_chat_message_doc,
    chat_message_to_dict,
)
from nlp.nlp_engine import get_nlp_engine

_nlp_engine = get_nlp_engine()

STREAM_ROOM_PREFIX = "stream_"


def build_stream_message_data(stream_id, user_id, message_text, is_host=False):
    """Analyze, persist, and enrich a stream chat message.

    Shares a single NLP + persistence code path between the SocketIO
    ``send_message`` handler and the REST ``/api/streams/send_message``
    endpoint.  Returns the row dict with sentiment/intent badges, or
    ``None`` when the stream or user is invalid.
    """
    sentiment = _nlp_engine.analyze_sentiment(message_text)
    intent = _nlp_engine.detect_intent(message_text)

    with session_scope() as s:
        # Verify stream + user exist
        stream = s["live_streams"].find_one({"_id": stream_id})
        user = s["users"].find_one({"_id": user_id})
        if not stream or not user:
            return None

        message_doc = make_chat_message_doc(
            stream_id=stream_id,
            user_id=user_id,
            message_text=message_text,
            sentiment_score=sentiment["sentiment_score"],
            sentiment_label=SentimentLabel(sentiment["sentiment_label"]).value,
            intent_tag=IntentTag(intent).value,
            is_host=bool(is_host),
        )
        message_doc["_id"] = _next_id(s.db)
        message_doc.setdefault("created_at", _now_iso())
        s["chat_messages"].insert_one(message_doc)

        result = chat_message_to_dict(
            message_doc,
            username=user.get("username", ""),
        )

    # Sentiment / intent badges are presentation hints; keep them in the payload.
    sentiment_emoji = "🟢" if sentiment["sentiment_label"] == "POSITIVE" else (
        "🔴" if sentiment["sentiment_label"] == "NEGATIVE" else "🟡"
    )
    intent_emoji = {
        "PRICE_INQUIRY": "💰",
        "QUALITY_INQUIRY": "🌿",
        "DELIVERY_INQUIRY": "🚚",
        "GENERAL_CHAT": "💬",
    }.get(intent, "💬")

    result["sentiment_badge"] = sentiment_emoji
    result["intent_badge"] = intent_emoji
    return result


def register_socketio_handlers(socketio) -> None:
    @socketio.on("connect")
    def handle_connect(auth=None):
        emit("connected", {"message": "Connected to Agritech Marketplace"})

    @socketio.on("disconnect")
    def handle_disconnect():
        pass

    @socketio.on("join_stream")
    def handle_join_stream(data):
        stream_id = (data or {}).get("stream_id")
        if not stream_id:
            emit("error", {"message": "stream_id required"})
            return

        room = f"{STREAM_ROOM_PREFIX}{stream_id}"
        join_room(room)

        # Fetch recent messages for this stream
        with session_scope() as s:
            messages = list(
                s["chat_messages"]
                .find({"stream_id": stream_id})
                .sort("timestamp", -1)
                .limit(50)
            )
            message_list = []
            for m in reversed(messages):
                user = s["users"].find_one({"_id": m.get("user_id")})
                message_list.append(
                    chat_message_to_dict(m, username=user.get("username") if user else None)
                )

        emit(
            "stream_joined",
            {
                "stream_id": stream_id,
                "room": room,
                "recent_messages": message_list,
            },
            to=room,
        )

    @socketio.on("leave_stream")
    def handle_leave_stream(data):
        stream_id = (data or {}).get("stream_id")
        if stream_id:
            room = f"{STREAM_ROOM_PREFIX}{stream_id}"
            leave_room(room)
            emit("stream_left", {"stream_id": stream_id}, to=room)

    @socketio.on("send_message")
    def handle_send_message(data):
        """Receive chat text from buyer -> process through NLP -> store -> broadcast."""
        stream_id = (data or {}).get("stream_id")
        user_id = (data or {}).get("user_id")
        message_text = (data or {}).get("message_text", "").strip()
        is_host = bool((data or {}).get("is_host", False))

        if not stream_id or not user_id or not message_text:
            emit("error", {"message": "stream_id, user_id, and message_text required"})
            return

        message_data = build_stream_message_data(stream_id, user_id, message_text, is_host=is_host)
        if message_data is None:
            emit("error", {"message": "Invalid stream or user"})
            return

        room = f"{STREAM_ROOM_PREFIX}{stream_id}"
        emit("new_message", message_data, to=room)

    @socketio.on("get_stream_state")
    def handle_get_stream_state(data):
        stream_id = (data or {}).get("stream_id")
        if not stream_id:
            emit("error", {"message": "stream_id required"})
            return

        with session_scope() as s:
            stream = s["live_streams"].find_one({"_id": stream_id})
            if not stream:
                emit("error", {"message": "stream not found"})
                return

            # Get sentiment distribution for this stream
            messages = list(s["chat_messages"].find({"stream_id": stream_id}))
            sentiment_dist = {"POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0}
            intent_dist = {
                "PRICE_INQUIRY": 0,
                "QUALITY_INQUIRY": 0,
                "DELIVERY_INQUIRY": 0,
                "GENERAL_CHAT": 0,
            }

            for msg in messages:
                label = msg.get("sentiment_label", "NEUTRAL")
                sentiment_dist[label] = sentiment_dist.get(label, 0) + 1
                intent_dist[msg.get("intent_tag", "GENERAL_CHAT")] = intent_dist.get(
                    msg.get("intent_tag", "GENERAL_CHAT"), 0
                ) + 1

        # Reconstruct stream dict shape for the frontend
        stream_dict = {
            "id": stream.get("_id"),
            "seller_id": stream.get("seller_id"),
            "stream_title": stream.get("stream_title", ""),
            "is_active": stream.get("is_active", False),
            "started_at": stream.get("started_at"),
        }

        emit(
            "stream_state",
            {
                "stream": stream_dict,
                "sentiment_distribution": sentiment_dist,
                "intent_distribution": intent_dist,
                "total_messages": len(messages),
            },
        )

    @socketio.on("ping")
    def handle_ping(data):
        emit("pong", {"timestamp": (data or {}).get("timestamp")})


# ---------------------------------------------------------------------------
# Helpers (shared with routes.py)
# ---------------------------------------------------------------------------

def _next_id(db):
    from pymongo import ReturnDocument
    counters = db["_counters"]
    result = counters.find_one_and_update(
        {"_id": "next_id"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(result["seq"])


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
