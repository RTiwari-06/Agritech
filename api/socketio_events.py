"""Socket.IO server logic — Redis-only chat pipeline.

``send_message`` -> VADER sentiment + keyword intent (AgriNLPEngine) ->
spec §3.4 payload -> Redis capped List (``stream:<id>:chats``, default stream
``1``) -> broadcast to the stream room.  No session_scope, no database session
at all.
"""

from datetime import datetime, timezone
from uuid import uuid4

from flask_socketio import emit, join_room, leave_room

from database.redis_db import DEFAULT_STREAM_ID, push_chat_message
from nlp.nlp_engine import get_nlp_engine

_nlp_engine = get_nlp_engine()

STREAM_ROOM_PREFIX = "stream_"


def register_socketio_handlers(socketio) -> None:
    @socketio.on("connect")
    def handle_connect(auth=None):
        # Auth layer removed (Phase 0); every socket may connect and post.
        emit("connected", {"message": "Connected to Agritech Marketplace"})

    @socketio.on("join_stream")
    def handle_join_stream(data):
        data = data or {}
        stream_id = data.get("stream_id")
        if not stream_id:
            emit("error", {"message": "stream_id required"})
            return
        join_room(f"{STREAM_ROOM_PREFIX}{stream_id}")
        emit("joined", {"stream_id": stream_id})

    @socketio.on("leave_stream")
    def handle_leave_stream(data):
        data = data or {}
        stream_id = data.get("stream_id")
        if not stream_id:
            emit("error", {"message": "stream_id required"})
            return
        leave_room(f"{STREAM_ROOM_PREFIX}{stream_id}")
        emit("stream_left", {"stream_id": stream_id})

    @socketio.on("send_message")
    def handle_send_message(data):
        """Chat ingestion -> NLP metrics -> spec §3.4 payload -> Redis -> room broadcast."""
        data = data or {}
        stream_id = str(data.get("stream_id") or DEFAULT_STREAM_ID)
        message_text = (data.get("message_text") or data.get("message") or "").strip()

        if not message_text:
            emit("error", {"message": "message_text or message required"})
            return

        # Demo identity (auth removed): honour a stated user_id when given,
        # otherwise attribute to the generic demo buyer id 0.
        sender_id = data.get("user_id") or 0
        username = (data.get("username") or "").strip() or f"user_{sender_id}"
        sent_at = datetime.now(timezone.utc)

        sentiment = _nlp_engine.analyze_sentiment(message_text)
        intent = _nlp_engine.detect_intent(message_text)

        message_doc = {
            "id": uuid4().hex[:12],
            "stream_id": stream_id,
            "user_id": sender_id,
            "username": username,
            "is_host": bool(data.get("is_host")),
            "message": message_text,          # spec §3.4 alias
            "message_text": message_text,     # internal alias
            "sentiment": sentiment["sentiment_score"],       # spec §3.4 alias
            "sentiment_score": sentiment["sentiment_score"],  # internal alias
            "sentiment_label": sentiment["sentiment_label"],
            "intent_tag": intent,
            "timestamp": int(sent_at.timestamp()),
            "created_at": sent_at.isoformat(),
        }

        push_chat_message(message_doc)
        emit("new_message", message_doc, to=f"{STREAM_ROOM_PREFIX}{stream_id}")

    @socketio.on("ping")
    def handle_ping(data):
        emit("pong", {"timestamp": (data or {}).get("timestamp")})
