"""SocketIO server logic: live marketplace feed, chat, and streaming."""

from flask_socketio import emit, join_room, leave_room

from database.db import session_scope
from database.models import (
    ChatMessage,
    IntentTag,
    LiveStream,
    SentimentLabel,
    User,
)
from nlp.nlp_engine import get_nlp_engine

_nlp_engine = get_nlp_engine()

STREAM_ROOM_PREFIX = "stream_"


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
        with session_scope() as session:
            messages = (
                session.query(ChatMessage)
                .filter(ChatMessage.stream_id == stream_id)
                .order_by(ChatMessage.timestamp.desc())
                .limit(50)
                .all()
            )
            message_list = [m.to_dict() for m in reversed(messages)]

        emit("stream_joined", {
            "stream_id": stream_id,
            "room": room,
            "recent_messages": message_list
        }, to=room)

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

        if not stream_id or not user_id or not message_text:
            emit("error", {"message": "stream_id, user_id, and message_text required"})
            return

        # Process through NLP
        sentiment = _nlp_engine.analyze_sentiment(message_text)
        intent = _nlp_engine.detect_intent(message_text)

        # Store in database
        with session_scope() as session:
            stream = session.get(LiveStream, stream_id)
            user = session.get(User, user_id)

            if not stream or not user:
                emit("error", {"message": "Invalid stream or user"})
                return

            chat_msg = ChatMessage(
                stream_id=stream.id,
                user_id=user.id,
                message_text=message_text,
                sentiment_score=sentiment["sentiment_score"],
                sentiment_label=SentimentLabel(sentiment["sentiment_label"]),
                intent_tag=IntentTag(intent),
            )
            session.add(chat_msg)
            session.flush()
            message_data = chat_msg.to_dict()

        # Broadcast to stream room
        room = f"{STREAM_ROOM_PREFIX}{stream_id}"

        # Add sentiment badge
        sentiment_emoji = "🟢" if sentiment["sentiment_label"] == "POSITIVE" else ("🔴" if sentiment["sentiment_label"] == "NEGATIVE" else "🟡")
        intent_emoji = {
            "PRICE_INQUIRY": "💰",
            "QUALITY_INQUIRY": "🌿",
            "DELIVERY_INQUIRY": "🚚",
            "GENERAL_CHAT": "💬"
        }.get(intent, "💬")

        message_data["sentiment_badge"] = sentiment_emoji
        message_data["intent_badge"] = intent_emoji

        emit("new_message", message_data, to=room)

    @socketio.on("get_stream_state")
    def handle_get_stream_state(data):
        stream_id = (data or {}).get("stream_id")
        if not stream_id:
            emit("error", {"message": "stream_id required"})
            return

        with session_scope() as session:
            stream = session.get(LiveStream, stream_id)
            if not stream:
                emit("error", {"message": "stream not found"})
                return

            # Get sentiment distribution for this stream
            messages = session.query(ChatMessage).filter(ChatMessage.stream_id == stream_id).all()
            sentiment_dist = {"POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0}
            intent_dist = {"PRICE_INQUIRY": 0, "QUALITY_INQUIRY": 0, "DELIVERY_INQUIRY": 0, "GENERAL_CHAT": 0}

            for msg in messages:
                label = msg.sentiment_label.value if msg.sentiment_label else "NEUTRAL"
                sentiment_dist[label] = sentiment_dist.get(label, 0) + 1
                intent_dist[msg.intent_tag.value] = intent_dist.get(msg.intent_tag.value, 0) + 1

        emit("stream_state", {
            "stream": stream.to_dict(),
            "sentiment_distribution": sentiment_dist,
            "intent_distribution": intent_dist,
            "total_messages": len(messages)
        })

    @socketio.on("ping")
    def handle_ping(data):
        emit("pong", {"timestamp": data.get("timestamp")})