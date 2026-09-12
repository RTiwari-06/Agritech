"""SQLAlchemy ORM models for the agricultural marketplace.

Entities:
    User         - a platform user (seller or buyer)
    Product      - a produce listing with live price and stock
    LiveStream   - a seller's live streaming session
    ChatMessage  - real-time chat messages during live streams
    Review       - buyer feedback with sentiment analysis
    Order        - purchase orders
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum as SQLEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from database.connection import Base


def _utcnow() -> datetime:
    return datetime.utcnow()


class UserRole(str, Enum):
    SELLER = "seller"
    BUYER = "buyer"


class ProductCategory(str, Enum):
    GRAIN = "Grain"
    VEGETABLE = "Vegetable"
    FRUIT = "Fruit"
    ORGANIC = "Organic"


class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class SentimentLabel(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"


class IntentTag(str, Enum):
    PRICE_INQUIRY = "PRICE_INQUIRY"
    QUALITY_INQUIRY = "QUALITY_INQUIRY"
    DELIVERY_INQUIRY = "DELIVERY_INQUIRY"
    GENERAL_CHAT = "GENERAL_CHAT"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email", "email", unique=True),
        Index("ix_users_role", "role"),
    )

    id = Column(Integer, primary_key=True)
    username = Column(String(80), nullable=False, unique=True)
    email = Column(String(120), nullable=False, unique=True)
    role = Column(SQLEnum(UserRole), nullable=False, default=UserRole.BUYER)
    location = Column(String(120), default="")
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    products = relationship(
        "Product", back_populates="seller", cascade="all, delete-orphan"
    )
    streams = relationship(
        "LiveStream", back_populates="seller", cascade="all, delete-orphan"
    )
    chat_messages = relationship(
        "ChatMessage", back_populates="user", cascade="all, delete-orphan"
    )
    reviews = relationship(
        "Review", back_populates="user", cascade="all, delete-orphan"
    )
    orders = relationship(
        "Order", back_populates="buyer", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role.value if self.role else None,
            "location": self.location,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_category", "category"),
        Index("ix_products_seller", "seller_id"),
    )

    id = Column(Integer, primary_key=True)
    seller_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String(160), nullable=False)
    category = Column(SQLEnum(ProductCategory), nullable=False, default=ProductCategory.VEGETABLE)
    description = Column(Text, default="")
    price = Column(Float, default=0.0, nullable=False)
    stock_kg = Column(Float, default=0.0, nullable=False)
    rating = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    seller = relationship("User", back_populates="products")
    reviews = relationship(
        "Review", back_populates="product", cascade="all, delete-orphan"
    )
    orders = relationship(
        "Order", back_populates="product", cascade="all, delete-orphan"
    )
    price_logs = relationship(
        "PriceLog", back_populates="product", cascade="all, delete-orphan"
    )

    def to_dict(self, include_seller: bool = True) -> dict:
        data = {
            "id": self.id,
            "seller_id": self.seller_id,
            "name": self.name,
            "category": self.category.value if self.category else None,
            "description": self.description,
            "price": self.price,
            "stock_kg": self.stock_kg,
            "quantity": self.stock_kg,  # backward compatibility
            "rating": self.rating,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_seller and self.seller is not None:
            data["seller"] = {
                "id": self.seller.id,
                "username": self.seller.username,
                "location": self.seller.location,
            }
        return data


class LiveStream(Base):
    __tablename__ = "live_streams"
    __table_args__ = (
        Index("ix_live_streams_seller", "seller_id"),
        Index("ix_live_streams_active", "is_active"),
    )

    id = Column(Integer, primary_key=True)
    seller_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    stream_title = Column(String(200), nullable=False)
    is_active = Column(String(20), default="false", nullable=False)
    started_at = Column(DateTime, default=_utcnow, nullable=False)

    seller = relationship("User", back_populates="streams")
    chat_messages = relationship(
        "ChatMessage", back_populates="stream", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "seller_id": self.seller_id,
            "stream_title": self.stream_title,
            "is_active": self.is_active == "true",
            "started_at": self.started_at.isoformat() if self.started_at else None,
        }


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_stream", "stream_id"),
        Index("ix_chat_messages_user", "user_id"),
        Index("ix_chat_messages_timestamp", "timestamp"),
    )

    id = Column(Integer, primary_key=True)
    stream_id = Column(Integer, ForeignKey("live_streams.id"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    message_text = Column(Text, nullable=False)
    sentiment_score = Column(Float, default=0.0, nullable=False)
    sentiment_label = Column(SQLEnum(SentimentLabel), default=SentimentLabel.NEUTRAL, nullable=False)
    intent_tag = Column(SQLEnum(IntentTag), default=IntentTag.GENERAL_CHAT, nullable=False)
    timestamp = Column(DateTime, default=_utcnow, nullable=False)

    stream = relationship("LiveStream", back_populates="chat_messages")
    user = relationship("User", back_populates="chat_messages")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "stream_id": self.stream_id,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "message_text": self.message_text,
            "sentiment_score": self.sentiment_score,
            "sentiment_label": self.sentiment_label.value if self.sentiment_label else None,
            "intent_tag": self.intent_tag.value if self.intent_tag else None,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating"),
        Index("ix_reviews_product", "product_id"),
        Index("ix_reviews_user", "user_id"),
    )

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    review_text = Column(Text, default="")
    rating = Column(Integer, nullable=False)
    sentiment_score = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    product = relationship("Product", back_populates="reviews")
    user = relationship("User", back_populates="reviews")

    def to_dict(self) -> dict:
        label = "POSITIVE" if self.sentiment_score > 0.15 else ("NEGATIVE" if self.sentiment_score < -0.15 else "NEUTRAL")
        return {
            "id": self.id,
            "product_id": self.product_id,
            "product_name": self.product.name if self.product else None,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "review_text": self.review_text,
            "rating": self.rating,
            "sentiment_score": self.sentiment_score,
            "sentiment_label": label.lower(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_buyer", "buyer_id"),
        Index("ix_orders_product", "product_id"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_created", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    buyer_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), index=True, nullable=False)
    quantity_kg = Column(Float, nullable=False)
    total_price = Column(Float, nullable=False)
    status = Column(SQLEnum(OrderStatus), default=OrderStatus.PENDING, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    buyer = relationship("User", back_populates="orders")
    product = relationship("Product", back_populates="orders")

    def to_dict(self) -> dict:
        unit_price = self.total_price / self.quantity_kg if self.quantity_kg else 0
        return {
            "id": self.id,
            "buyer_id": self.buyer_id,
            "buyer_username": self.buyer.username if self.buyer else None,
            "product_id": self.product_id,
            "product_name": self.product.name if self.product else None,
            "quantity_kg": self.quantity_kg,
            "quantity": self.quantity_kg,  # backward compatibility
            "unit_price": round(unit_price, 2),
            "total_price": self.total_price,
            "status": self.status.value if self.status else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PriceLog(Base):
    __tablename__ = "price_logs"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), index=True, nullable=False)
    old_price = Column(Float, nullable=False)
    new_price = Column(Float, nullable=False)
    reason = Column(String(120), default="")
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    product = relationship("Product", back_populates="price_logs")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "product_id": self.product_id,
            "product_name": self.product.name if self.product else None,
            "old_price": self.old_price,
            "new_price": self.new_price,
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }