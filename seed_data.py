"""Database seeder for the Agricultural Sales Optimization framework.

Rebuilds the MongoDB collections and loads realistic demo data:
  - 3 Agricultural Sellers
  - 5 Buyers
  - 10 Realistic Produce Products
  - 2 Live Streams
  - 20 Sample Customer Reviews
  - 30 Sample Live Chat Messages
  - Sample Orders

Usage:
    python seed_data.py
"""

import random
from datetime import datetime, timedelta, timezone

import numpy as np

from config import Config
from database.db import drop_all, init_db, get_db, get_client, hash_password
from database.models import (
    SentimentLabel,
    IntentTag,
    OrderStatus,
    ProductCategory,
    UserRole,
    make_user_doc,
    make_product_doc,
    make_live_stream_doc,
    make_chat_message_doc,
    make_review_doc,
    make_order_doc,
)
from nlp.nlp_engine import AgriNLPEngine


random.seed(42)
np.random.seed(42)

NLP_ENGINE = AgriNLPEngine()


SELLERS = [
    {
        "username": "ramesh_organic_farms",
        "email": "ramesh@organicfarms.in",
        "role": UserRole.SELLER.value,
        "location": "Nashik, Maharashtra",
    },
    {
        "username": "karnataka_produce_hub",
        "email": "sales@karnatakaproduce.com",
        "role": UserRole.SELLER.value,
        "location": "Bengaluru, Karnataka",
    },
    {
        "username": "punjab_grain_corp",
        "email": "orders@punjabgrain.in",
        "role": UserRole.SELLER.value,
        "location": "Ludhiana, Punjab",
    },
]

BUYERS = [
    {
        "username": "priya_sharma",
        "email": "priya.sharma@email.com",
        "role": UserRole.BUYER.value,
        "location": "Mumbai, Maharashtra",
    },
    {
        "username": "rajesh_kumar",
        "email": "rajesh.k@email.com",
        "role": UserRole.BUYER.value,
        "location": "Delhi NCR",
    },
    {
        "username": "anita_reddy",
        "email": "anita.reddy@email.com",
        "role": UserRole.BUYER.value,
        "location": "Hyderabad, Telangana",
    },
    {
        "username": "vikram_singh",
        "email": "vikram.singh@email.com",
        "role": UserRole.BUYER.value,
        "location": "Chennai, Tamil Nadu",
    },
    {
        "username": "sneha_patel",
        "email": "sneha.patel@email.com",
        "role": UserRole.BUYER.value,
        "location": "Ahmedabad, Gujarat",
    },
]

PRODUCTS = [
    {
        "seller_idx": 0,
        "name": "Organic Sona Masoori Rice",
        "category": ProductCategory.GRAIN.value,
        "description": "Premium organic Sona Masoori rice, grown without pesticides in the fertile fields of Nashik. Light, aromatic, and perfect for daily meals.",
        "price": 85.0,
        "stock_kg": 500.0,
        "rating": 4.8,
    },
    {
        "seller_idx": 0,
        "name": "Organic Toor Dal (Pigeon Pea)",
        "category": ProductCategory.ORGANIC.value,
        "description": "Certified organic Toor Dal, rich in protein and fiber. Stone-ground to preserve nutrients. Ideal for sambar and dal tadka.",
        "price": 140.0,
        "stock_kg": 300.0,
        "rating": 4.7,
    },
    {
        "seller_idx": 0,
        "name": "Fresh Organic Spinach",
        "category": ProductCategory.ORGANIC.value,
        "description": "Farm-fresh organic spinach harvested at dawn. Deep green leaves packed with iron and vitamins. Perfect for palak paneer and smoothies.",
        "price": 45.0,
        "stock_kg": 50.0,
        "rating": 4.9,
    },
    {
        "seller_idx": 1,
        "name": "Fresh Tomatoes (Desi Variety)",
        "category": ProductCategory.VEGETABLE.value,
        "description": "Juicy desi tomatoes grown in Karnataka's red soil. Rich flavor, perfect for curries, rasam, and fresh salads. Pesticide-free cultivation.",
        "price": 35.0,
        "stock_kg": 200.0,
        "rating": 4.6,
    },
    {
        "seller_idx": 1,
        "name": "Alphonso Mangoes (Ratnagiri)",
        "category": ProductCategory.FRUIT.value,
        "description": "World-famous Alphonso mangoes from Ratnagiri, GI-tagged. Sweet, fiberless, and aromatic. The king of mangoes, available seasonal.",
        "price": 450.0,
        "stock_kg": 100.0,
        "rating": 4.9,
    },
    {
        "seller_idx": 1,
        "name": "Organic Baby Bananas (Yelakki)",
        "category": ProductCategory.FRUIT.value,
        "description": "Sweet and tiny Yelakki bananas, organically grown in Karnataka. Naturally ripened, perfect for kids and healthy snacking.",
        "price": 60.0,
        "stock_kg": 150.0,
        "rating": 4.7,
    },
    {
        "seller_idx": 1,
        "name": "Fresh Green Beans",
        "category": ProductCategory.VEGETABLE.value,
        "description": "Crisp and tender green beans, harvested daily. Excellent for stir-fries, curries, and steaming. Farm-to-table freshness guaranteed.",
        "price": 55.0,
        "stock_kg": 120.0,
        "rating": 4.5,
    },
    {
        "seller_idx": 2,
        "name": "Premium Sharbati Wheat",
        "category": ProductCategory.GRAIN.value,
        "description": "Golden Sharbati wheat from Punjab's finest farms. High protein content, makes soft and fluffy rotis. Stone-ground atta available.",
        "price": 42.0,
        "stock_kg": 1000.0,
        "rating": 4.8,
    },
    {
        "seller_idx": 2,
        "name": "Organic Basmati Rice",
        "category": ProductCategory.ORGANIC.value,
        "description": "Long-grain organic Basmati rice, aged for 12 months for enhanced aroma. Perfect for biryani, pulao, and special occasions.",
        "price": 180.0,
        "stock_kg": 400.0,
        "rating": 4.9,
    },
    {
        "seller_idx": 2,
        "name": "Fresh Cauliflower",
        "category": ProductCategory.VEGETABLE.value,
        "description": "Compact white cauliflower heads, grown in Punjab's cool climate. Tight curds, no blemishes. Perfect for gobi paratha and manchurian.",
        "price": 30.0,
        "stock_kg": 250.0,
        "rating": 4.4,
    },
]

POSITIVE_REVIEW_TEXTS = [
    "Absolutely fresh and delicious! Best quality produce I've bought online.",
    "Excellent taste and perfectly ripe. Will definitely order again.",
    "Great product, fast delivery, everything arrived in perfect condition.",
    "Loved it! Fresh, tasty, and great value for money.",
    "Superb quality, worth every rupee. Highly recommend this seller.",
    "The mangoes were heavenly - sweet, fiberless, and aromatic!",
    "Rice quality is exceptional. Made the best biryani with this basmati.",
    "Vegetables are so fresh, you can taste the difference from market produce.",
    "Organic certification gives peace of mind. Great service too!",
    "Prompt delivery and excellent packaging. Everything farm fresh.",
]

NEGATIVE_REVIEW_TEXTS = [
    "Received wilted and bruised vegetables. Very disappointed in quality.",
    "Items arrived damaged and some were rotten. Not fresh at all.",
    "Overpriced for the small quantity received. Expected better value.",
    "Delivery was late and produce was not as described.",
    "Quality has declined compared to previous orders.",
]

NEUTRAL_REVIEW_TEXTS = [
    "Arrived on time, quality is average. Nothing special but acceptable.",
    "Reasonable product for the price. Packaging could be improved.",
    "Okay experience overall. Will see if I order again.",
    "Standard quality, nothing outstanding but does the job.",
]

CHAT_MESSAGES = [
    ("How much per kg for the tomatoes?", "priya_sharma"),
    ("Is the rice organic certified?", "rajesh_kumar"),
    ("What's the delivery time to Mumbai?", "anita_reddy"),
    ("Can I get a discount on 10kg?", "vikram_singh"),
    ("Are the mangoes naturally ripened?", "sneha_patel"),
    ("Do you ship to Delhi?", "priya_sharma"),
    ("What's the price for 5kg of wheat?", "rajesh_kumar"),
    ("Are these pesticide free?", "anita_reddy"),
    ("How fresh are the spinach leaves?", "vikram_singh"),
    ("Can you deliver to Bangalore tomorrow?", "sneha_patel"),
    ("Is there a bulk discount available?", "priya_sharma"),
    ("What variety of mangoes are these?", "rajesh_kumar"),
    ("Shipping charges to Hyderabad?", "anita_reddy"),
    ("Are the bananas organic?", "vikram_singh"),
    ("How long does delivery take?", "sneha_patel"),
    ("Price for 20kg rice?", "priya_sharma"),
    ("Certified organic?", "rajesh_kumar"),
    ("Delivery to Chennai available?", "anita_reddy"),
    ("Any offer on mangoes?", "vikram_singh"),
    ("Fresh stock available?", "sneha_patel"),
    ("What's the minimum order?", "priya_sharma"),
    ("Quality guarantee?", "rajesh_kumar"),
    ("Express delivery option?", "anita_reddy"),
    ("Packaging for long distance?", "vikram_singh"),
    ("Seasonal availability?", "sneha_patel"),
    ("Return policy if not fresh?", "priya_sharma"),
    ("Bulk pricing for restaurants?", "rajesh_kumar"),
    ("Cold chain delivery?", "anita_reddy"),
    ("Harvest date for vegetables?", "vikram_singh"),
    ("Payment options available?", "sneha_patel"),
]

SAMPLE_ORDERS = [
    {"buyer_idx": 0, "product_idx": 0, "quantity_kg": 10.0},
    {"buyer_idx": 1, "product_idx": 1, "quantity_kg": 5.0},
    {"buyer_idx": 2, "product_idx": 4, "quantity_kg": 3.0},
    {"buyer_idx": 3, "product_idx": 3, "quantity_kg": 8.0},
    {"buyer_idx": 4, "product_idx": 5, "quantity_kg": 6.0},
    {"buyer_idx": 0, "product_idx": 7, "quantity_kg": 25.0},
    {"buyer_idx": 1, "product_idx": 8, "quantity_kg": 15.0},
    {"buyer_idx": 2, "product_idx": 2, "quantity_kg": 2.0},
    {"buyer_idx": 3, "product_idx": 6, "quantity_kg": 4.0},
    {"buyer_idx": 4, "product_idx": 9, "quantity_kg": 12.0},
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _offset(days: int = 0, hours: int = 0) -> datetime:
    return _now() - timedelta(days=days, hours=hours)


def reset_and_seed() -> None:
    # Rebuild collections from scratch
    init_db()
    drop_all()
    init_db()

    db = get_db()

    # mongomock can lose the auto-seeded counter doc across drop+create.
    # Reset it cleanly so _next_id() starts from 1.
    db["_counters"].delete_many({})
    db["_counters"].insert_one({"_id": "next_id", "seq": 0})

    # Create sellers
    sellers = []
    for entry in SELLERS:
        doc = make_user_doc(
            username=entry["username"],
            email=entry["email"],
            role=entry["role"],
            location=entry["location"],
            password_hash=hash_password("demo-password-123"),
        )
        doc["_id"] = _next_id(db)
        doc.setdefault("created_at", _offset(hours=2).isoformat())
        db["users"].insert_one(doc)
        sellers.append(doc)

    # Create buyers
    buyers = []
    for entry in BUYERS:
        doc = make_user_doc(
            username=entry["username"],
            email=entry["email"],
            role=entry["role"],
            location=entry["location"],
            password_hash=hash_password("demo-password-123"),
        )
        doc["_id"] = _next_id(db)
        doc.setdefault("created_at", _offset(hours=2).isoformat())
        db["users"].insert_one(doc)
        buyers.append(doc)

    # Create products
    products = []
    for entry in PRODUCTS:
        doc = make_product_doc(
            seller_id=sellers[entry["seller_idx"]]["_id"],
            name=entry["name"],
            category=entry["category"],
            description=entry["description"],
            price=entry["price"],
            stock_kg=entry["stock_kg"],
            rating=entry["rating"],
        )
        doc["_id"] = _next_id(db)
        doc.setdefault("created_at", _offset(days=random.randint(0, 30)).isoformat())
        db["products"].insert_one(doc)
        products.append(doc)

    # Create live streams
    streams = []
    for i, seller in enumerate(sellers):
        doc = make_live_stream_doc(
            seller_id=seller["_id"],
            stream_title=f"Fresh Harvest Live from {seller['location']} - Day {i+1}",
            is_active=i < 2,
        )
        doc["_id"] = _next_id(db)
        doc.setdefault("started_at", _offset(hours=2).isoformat())
        db["live_streams"].insert_one(doc)
        streams.append(doc)

    # Create reviews with sentiment analysis
    review_texts = POSITIVE_REVIEW_TEXTS + NEGATIVE_REVIEW_TEXTS + NEUTRAL_REVIEW_TEXTS
    for _ in range(20):
        product = random.choice(products)
        buyer = random.choice(buyers)
        text = random.choice(review_texts)
        sentiment = NLP_ENGINE.analyze_sentiment(text)
        rating_map = {"POSITIVE": 5, "NEGATIVE": 1, "NEUTRAL": 3}
        rating = rating_map.get(sentiment["sentiment_label"], 3)

        doc = make_review_doc(
            product_id=product["_id"],
            user_id=buyer["_id"],
            review_text=text,
            rating=rating,
            sentiment_score=sentiment["sentiment_score"],
        )
        doc["_id"] = _next_id(db)
        doc.setdefault("created_at", _offset(days=random.randint(0, 30)).isoformat())
        db["reviews"].insert_one(doc)

        # Update product rating (simple average)
        all_reviews = list(db["reviews"].find({"product_id": product["_id"]}))
        if all_reviews:
            new_rating = sum(r.get("rating", 0) for r in all_reviews) / len(all_reviews)
            db["products"].update_one(
                {"_id": product["_id"]},
                {"$set": {"rating": round(new_rating, 2)}},
            )

    # Create chat messages with NLP analysis
    for text, buyer_username in CHAT_MESSAGES:
        buyer = next(b for b in buyers if b["username"] == buyer_username)
        stream = random.choice(streams)
        sentiment = NLP_ENGINE.analyze_sentiment(text)
        intent = NLP_ENGINE.detect_intent(text)

        doc = make_chat_message_doc(
            stream_id=stream["_id"],
            user_id=buyer["_id"],
            message_text=text,
            sentiment_score=sentiment["sentiment_score"],
            sentiment_label=SentimentLabel(sentiment["sentiment_label"]).value,
            intent_tag=IntentTag(intent).value,
        )
        doc["_id"] = _next_id(db)
        doc.setdefault("timestamp", _offset(days=random.randint(0, 30)).isoformat())
        db["chat_messages"].insert_one(doc)

    # Create orders
    for order_data in SAMPLE_ORDERS:
        buyer = buyers[order_data["buyer_idx"]]
        product = products[order_data["product_idx"]]
        quantity = order_data["quantity_kg"]

        current_stock = product.get("stock_kg", 0)
        if current_stock >= quantity:
            total_price = round(product.get("price", 0) * quantity, 2)
            doc = make_order_doc(
                buyer_id=buyer["_id"],
                product_id=product["_id"],
                quantity_kg=quantity,
                total_price=total_price,
                status=OrderStatus.CONFIRMED.value,
            )
            doc["_id"] = _next_id(db)
            doc.setdefault("created_at", _offset(days=random.randint(0, 30)).isoformat())
            db["orders"].insert_one(doc)

            # Decrement stock
            db["products"].update_one(
                {"_id": product["_id"]},
                {"$inc": {"stock_kg": -quantity}},
            )

    print("Database seeded successfully!")
    print(f"  Sellers: {len(SELLERS)}")
    print(f"  Buyers: {len(BUYERS)}")
    print(f"  Products: {len(PRODUCTS)}")
    print(f"  Live Streams: {len(streams)}")
    print(f"  Reviews: 20")
    print(f"  Chat Messages: {len(CHAT_MESSAGES)}")
    print(f"  Orders: {len(SAMPLE_ORDERS)}")
    repo = "mongomock" if "mongomock" in repr(get_client()) else "mongodb"
    print(f"Database: {Config.MONGO_DB} (MongoDB via {repo})")


def _next_id(db):
    from pymongo import ReturnDocument
    counters = db["_counters"]
    doc = counters.find_one_and_update(
        {"_id": "next_id"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["seq"])


if __name__ == "__main__":
    reset_and_seed()
