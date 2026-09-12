"""Natural-language processing: semantic text analysis and sentiment scoring."""

from nlp.nlp_engine import (
    AgriNLPEngine,
    analyze_sentiment,
    compute_semantic_similarity,
    detect_intent,
    extract_keywords,
    get_nlp_engine,
)
from nlp.processing import (
    clean_text,
    embed_text,
    lemmatize,
    text_similarity,
    tokenize,
)
from nlp.sentiment import SentimentAnalyzer

__all__ = [
    "AgriNLPEngine",
    "analyze_sentiment",
    "compute_semantic_similarity",
    "detect_intent",
    "extract_keywords",
    "get_nlp_engine",
    "clean_text",
    "embed_text",
    "lemmatize",
    "text_similarity",
    "tokenize",
    "SentimentAnalyzer",
]