"""Database seeder for the Agricultural Sales Optimization framework.

Rebuilds the schema and loads realistic demo data:
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
from datetime import datetime, timedelta

import numpy as np

from config import Config
from database.db import drop_all, get_session, init_db
from database.models import (
    ChatMessage,
    IntentTag,
    LiveStream,
    Order,
    OrderStatus,
    Product,
    ProductCategory,
    Review,
    SentimentLabel,
    User,
    UserRole,
)
from nlp.nlp_engine import AgriNLPEngine


random.seed(42)
np.random.seed(42)

NLP_ENGINE = AgriNLPEngine()


SELLERS = [
    {
        "username": "ramesh_organic_farms",
        "email": "ramesh@organicfarms.in",
        "role": UserRole.SELLER,
        "location": "Nashik, Maharashtra",
    },
    {
        "username": "karnataka_produce_hub",
        "email": "sales@karnatakaproduce.com",
        "role": UserRole.SELLER,
        "location": "Bengaluru, Karnataka",
    },
    {
        "username": "punjab_grain_corp",
        "email": "orders@punjabgrain.in",
        "role": UserRole.SELLER,
        "location": "Ludhiana, Punjab",
    },
]

BUYERS = [
    {
        "username": "priya_sharma",
        "email": "priya.sharma@email.com",
        "role": UserRole.BUYER,
        "location": "Mumbai, Maharashtra",
    },
    {
        "username": "rajesh_kumar",
        "email": "rajesh.k@email.com",
        "role": UserRole.BUYER,
        "location": "Delhi NCR",
    },
    {
        "username": "anita_reddy",
        "email": "anita.reddy@email.com",
        "role": UserRole.BUYER,
        "location": "Hyderabad, Telangana",
    },
    {
        "username": "vikram_singh",
        "email": "vikram.singh@email.com",
        "role": UserRole.BUYER,
        "location": "Chennai, Tamil Nadu",
    },
    {
        "username": "sneha_patel",
        "email": "sneha.patel@email.com",
        "role": UserRole.BUYER,
        "location": "Ahmedabad, Gujarat",
    },
]

PRODUCTS = [
    {
        "seller_idx": 0,
        "name": "Organic Sona Masoori Rice",
        "category": ProductCategory.GRAIN,
        "description": "Premium organic Sona Masoori rice, grown without pesticides in the fertile fields of Nashik. Light, aromatic, and perfect for daily meals.",
        "price": 85.0,
        "stock_kg": 500.0,
        "rating": 4.8,
    },
    {
        "seller_idx": 0,
        "name": "Organic Toor Dal (Pigeon Pea)",
        "category": ProductCategory.ORGANIC,
        "description": "Certified organic Toor Dal, rich in protein and fiber. Stone-ground to preserve nutrients. Ideal for sambar and dal tadka.",
        "price": 140.0,
        "stock_kg": 300.0,
        "rating": 4.7,
    },
    {
        "seller_idx": 0,
        "name": "Fresh Organic Spinach",
        "category": ProductCategory.ORGANIC,
        "description": "Farm-fresh organic spinach harvested at dawn. Deep green leaves packed with iron and vitamins. Perfect for palak paneer and smoothies.",
        "price": 45.0,
        "stock_kg": 50.0,
        "rating": 4.9,
    },
    {
        "seller_idx": 1,
        "name": "Fresh Tomatoes (Desi Variety)",
        "category": ProductCategory.VEGETABLE,
        "description": "Juicy desi tomatoes grown in Karnataka's red soil. Rich flavor, perfect for curries, rasam, and fresh salads. Pesticide-free cultivation.",
        "price": 35.0,
        "stock_kg": 200.0,
        "rating": 4.6,
    },
    {
        "seller_idx": 1,
        "name": "Alphonso Mangoes (Ratnagiri)",
        "category": ProductCategory.FRUIT,
        "description": "World-famous Alphonso mangoes from Ratnagiri, GI-tagged. Sweet, fiberless, and aromatic. The king of mangoes, available seasonal.",
        "price": 450.0,
        "stock_kg": 100.0,
        "rating": 4.9,
    },
    {
        "seller_idx": 1,
        "name": "Organic Baby Bananas (Yelakki)",
        "category": ProductCategory.FRUIT,
        "description": "Sweet and tiny Yelakki bananas, organically grown in Karnataka. Naturally ripened, perfect for kids and healthy snacking.",
        "price": 60.0,
        "stock_kg": 150.0,
        "rating": 4.7,
    },
    {
        "seller_idx": 1,
        "name": "Fresh Green Beans",
        "category": ProductCategory.VEGETABLE,
        "description": "Crisp and tender green beans, harvested daily. Excellent for stir-fries, curries, and steaming. Farm-to-table freshness guaranteed.",
        "price": 55.0,
        "stock_kg": 120.0,
        "rating": 4.5,
    },
    {
        "seller_idx": 2,
        "name": "Premium Sharbati Wheat",
        "category": ProductCategory.GRAIN,
        "description": "Golden Sharbati wheat from Punjab's finest farms. High protein content, makes soft and fluffy rotis. Stone-ground atta available.",
        "price": 42.0,
        "stock_kg": 1000.0,
        "rating": 4.8,
    },
    {
        "seller_idx": 2,
        "name": "Organic Basmati Rice",
        "category": ProductCategory.ORGANIC,
        "description": "Long-grain organic Basmati rice, aged for 12 months for enhanced aroma. Perfect for biryani, pulao, and special occasions.",
        "price": 180.0,
        "stock_kg": 400.0,
        "rating": 4.9,
    },
    {
        "seller_idx": 2,
        "name": "Fresh Cauliflower",
        "category": ProductCategory.VEGETABLE,
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


def reset_and_seed() -> None:
    init_db()
    drop_all()
    init_db()

    with get_session() as session:
        # Create sellers
        sellers = []
        for entry in SELLERS:
            seller = User(
                username=entry["username"],
                email=entry["email"],
                role=entry["role"],
                location=entry["location"],
            )
            session.add(seller)
            sellers.append(seller)
        session.flush()

        # Create buyers
        buyers = []
        for entry in BUYERS:
            buyer = User(
                username=entry["username"],
                email=entry["email"],
                role=entry["role"],
                location=entry["location"],
            )
            session.add(buyer)
            buyers.append(buyer)
        session.flush()

        # Create products
        products = []
        for entry in PRODUCTS:
            product = Product(
                seller_id=sellers[entry["seller_idx"]].id,
                name=entry["name"],
                category=entry["category"],
                description=entry["description"],
                price=entry["price"],
                stock_kg=entry["stock_kg"],
                rating=entry["rating"],
            )
            session.add(product)
            products.append(product)
        session.flush()

        # Create live streams
        streams = []
        for i, seller in enumerate(sellers):
            stream = LiveStream(
                seller_id=seller.id,
                stream_title=f"Fresh Harvest Live from {seller.location} - Day {i+1}",
                is_active="true" if i < 2 else "false",
                started_at=datetime.utcnow() - timedelta(hours=2),
            )
            session.add(stream)
            streams.append(stream)
        session.flush()

        # Create reviews with sentiment analysis
        review_texts = POSITIVE_REVIEW_TEXTS + NEGATIVE_REVIEW_TEXTS + NEUTRAL_REVIEW_TEXTS
        for _ in range(20):
            product = random.choice(products)
            buyer = random.choice(buyers)
            text = random.choice(review_texts)
            sentiment = NLP_ENGINE.analyze_sentiment(text)
            rating_map = {"positive": 5, "negative": 1, "neutral": 3}
            rating = rating_map.get(sentiment["sentiment_label"].lower(), 3)

            review = Review(
                product_id=product.id,
                user_id=buyer.id,
                review_text=text,
                rating=rating,
                sentiment_score=sentiment["sentiment_score"],
            )
            session.add(review)

            # Update product rating (simple average)
            product_reviews = session.query(Review).filter(Review.product_id == product.id).all()
            if product_reviews:
                product.rating = sum(r.rating for r in product_reviews) / len(product_reviews)

        # Create chat messages with NLP analysis
        for i, (text, buyer_username) in enumerate(CHAT_MESSAGES):
            buyer = next(b for b in buyers if b.username == buyer_username)
            stream = random.choice(streams)
            sentiment = NLP_ENGINE.analyze_sentiment(text)
            intent = NLP_ENGINE.detect_intent(text)

            chat_msg = ChatMessage(
                stream_id=stream.id,
                user_id=buyer.id,
                message_text=text,
                sentiment_score=sentiment["sentiment_score"],
                sentiment_label=SentimentLabel(sentiment["sentiment_label"].upper()),
                intent_tag=IntentTag(intent),
            )
            session.add(chat_msg)

        # Create orders
        for order_data in SAMPLE_ORDERS:
            buyer = buyers[order_data["buyer_idx"]]
            product = products[order_data["product_idx"]]
            quantity = order_data["quantity_kg"]

            if product.stock_kg >= quantity:
                total_price = round(product.price * quantity, 2)
                order = Order(
                    buyer_id=buyer.id,
                    product_id=product.id,
                    quantity_kg=quantity,
                    total_price=total_price,
                    status=OrderStatus.CONFIRMED,
                    created_at=datetime.utcnow() - timedelta(days=random.randint(0, 30)),
                )
                session.add(order)
                product.stock_kg = round(product.stock_kg - quantity, 2)

        session.commit()

    print("Database seeded successfully!")
    print(f"  Sellers: {len(SELLERS)}")
    print(f"  Buyers: {len(BUYERS)}")
    print(f"  Products: {len(PRODUCTS)}")
    print(f"  Live Streams: {len(streams)}")
    print(f"  Reviews: 20")
    print(f"  Chat Messages: {len(CHAT_MESSAGES)}")
    print(f"  Orders: {len(SAMPLE_ORDERS)}")
    print(f"Database: {Config.effective_database_uri()}")


if __name__ == "__main__":
    reset_and_seed()