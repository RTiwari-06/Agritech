"""Comprehensive NLP pipeline for agricultural marketplace.

Combines SpaCy for linguistic processing, VADER for sentiment analysis,
and HuggingFace Transformers for advanced classification with robust fallbacks.
"""

import logging
import re
from typing import Any, Dict, List, Optional

import numpy as np

from config import Config

logger = logging.getLogger(__name__)

try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    spacy = None

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False

try:
    # Deferred import — importing transformers can pull in torch/CUDA and block
    # module boot. Keep the flag False until someone actually asks for the pipeline.
    _transformers_imported = False
    _pipeline_fn = None
except ImportError:
    _transformers_imported = False
    _pipeline_fn = None

def _ensure_transformers() -> Any:
    """Lazy-load the transformers ``pipeline`` import.

    Returns the ``pipeline`` callable, or ``None`` if unavailable.
    Called on first request, not at module import time.
    """
    global _transformers_imported, _pipeline_fn
    if _transformers_imported:
        return _pipeline_fn
    try:
        from transformers import pipeline as _pipeline
        _transformers_imported = True
        _pipeline_fn = _pipeline
        return _pipeline_fn
    except ImportError:
        return None

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


INTENT_KEYWORDS = {
    "PRICE_INQUIRY": [
        "price", "cost", "how much", "rate", "per kg", "per kilo", "discount",
        "offer", "deal", "cheap", "expensive", "affordable", "budget", "quote",
        "pricing", "rs.", "rupees", "₹", "amount", "charge", "fee", "tariff"
    ],
    "QUALITY_INQUIRY": [
        "fresh", "organic", "quality", "certified", "pesticide", "chemical",
        "natural", "ripe", "grade", "premium", "standard", "pure", "authentic",
        "farm fresh", "harvest", "cultivation", "grown", "variety", "type",
        "freshness", "shelf life", "expiry", "best before", "origin", "source"
    ],
    "DELIVERY_INQUIRY": [
        "delivery", "ship", "shipping", "dispatch", "courier", "transport",
        "logistics", "arrive", "reach", "send", "post", "dispatch", "transit",
        "location", "address", "pin code", "pincode", "area", "city", "state",
        "time", "days", "when", "how long", "eta", "timeline", "schedule",
        "packaging", "packed", "cold chain", "refrigerated", "cold storage"
    ],
}


SENTIMENT_LABELS = ["NEGATIVE", "NEUTRAL", "POSITIVE"]


class AgriNLPEngine:
    """Unified NLP engine for agricultural marketplace text processing."""

    def __init__(self) -> None:
        self._nlp = None
        self._vader = None
        self._transformer_sentiment = None
        self._transformer_intent = None
        self._tfidf_vectorizer = None
        self._tfidf_fitted = False
        self._corpus_texts: List[str] = []
        self._initialize_components()

    def _initialize_components(self) -> None:
        """Initialize all NLP components with graceful fallbacks."""
        self._load_spacy()
        self._load_vader()
        self._load_transformers()

    def _load_spacy(self) -> None:
        """Load SpaCy model with auto-download fallback."""
        if not SPACY_AVAILABLE:
            logger.warning("SpaCy not available. Using regex-based tokenization.")
            return
        try:
            self._nlp = spacy.load(Config.NLP_MODEL)
            logger.info(f"Loaded SpaCy model: {Config.NLP_MODEL}")
        except OSError:
            if Config.AUTO_DOWNLOAD_NLP_MODEL:
                try:
                    import subprocess
                    import sys
                    logger.info(f"Downloading SpaCy model: {Config.NLP_MODEL}")
                    subprocess.check_call(
                        [sys.executable, "-m", "spacy", "download", Config.NLP_MODEL]
                    )
                    try:
                        self._nlp = spacy.load(Config.NLP_MODEL)
                        logger.info(f"Downloaded and loaded SpaCy model: {Config.NLP_MODEL}")
                    except Exception as e:
                        logger.warning(f"Download succeeded but spacy.load failed: {e}")
                        self._nlp = None
                except Exception as e:
                    logger.warning(f"Failed to download SpaCy model: {e}")
                    self._nlp = None
            else:
                logger.warning(f"SpaCy model {Config.NLP_MODEL} not found. Set AUTO_DOWNLOAD_NLP_MODEL=true to auto-download.")
                self._nlp = None
        except Exception as e:
            logger.warning(f"Failed to load SpaCy: {e}")
            self._nlp = None

    def _load_vader(self) -> None:
        """Load VADER sentiment analyzer."""
        if VADER_AVAILABLE:
            try:
                self._vader = SentimentIntensityAnalyzer()
                logger.info("Loaded VADER sentiment analyzer")
            except Exception as e:
                logger.warning(f"Failed to load VADER: {e}")
                self._vader = None
        else:
            logger.warning("VADER not available. Using basic lexicon fallback.")

    def _load_transformers(self) -> None:
        """No-op — transformer loading is lazy in _get_transformer_sentiment()."""
        pass

    def _get_transformer_sentiment(self):
        """Lazily load transformer sentiment pipeline.

        Retries on every call so transient failures don't permanently disable
        the transformer layer for the process lifetime.
        """
        pipeline_fn = _ensure_transformers()
        if pipeline_fn is not None:
            if self._transformer_sentiment is None:
                try:
                    self._transformer_sentiment = pipeline_fn(
                        "sentiment-analysis",
                        model=Config.SENTIMENT_MODEL,
                        device=-1,
                        return_all_scores=False,
                    )
                    logger.info(f"Loaded transformer sentiment model: {Config.SENTIMENT_MODEL}")
                except Exception as e:
                    logger.debug(f"Transformer sentiment unavailable: {e}")
                    self._transformer_sentiment = None
            return self._transformer_sentiment
        return None

    def _get_transformer_intent(self):
        """Lazily load transformer zero-shot classification for intent.

        Retries on every call so transient failures don't permanently disable
        the transformer layer for the process lifetime.
        """
        pipeline_fn = _ensure_transformers()
        if pipeline_fn is not None:
            if self._transformer_intent is None:
                try:
                    self._transformer_intent = pipeline_fn(
                        "zero-shot-classification",
                        model=Config.INTENT_MODEL,
                        device=-1,
                    )
                    logger.info(f"Loaded transformer intent model: {Config.INTENT_MODEL}")
                except Exception as e:
                    logger.debug(f"Transformer intent unavailable: {e}")
                    self._transformer_intent = None
            return self._transformer_intent
        return None

    def analyze_sentiment(self, text: str) -> Dict[str, Any]:
        """Analyze sentiment of text.

        Returns:
            Dict with keys: sentiment_score (-1.0 to 1.0), sentiment_label (POSITIVE/NEUTRAL/NEGATIVE)
        """
        if not text or not str(text).strip():
            return {"sentiment_score": 0.0, "sentiment_label": "NEUTRAL"}

        text = str(text).strip()

        # Try transformer first
        transformer = self._get_transformer_sentiment()
        if transformer is not None:
            try:
                result = transformer(text[:512])[0]
                label = result["label"].upper()
                score = float(result["score"])

                if "NEG" in label:
                    sentiment_score = -score
                    sentiment_label = "NEGATIVE"
                elif "POS" in label:
                    sentiment_score = score
                    sentiment_label = "POSITIVE"
                else:
                    sentiment_score = 0.0
                    sentiment_label = "NEUTRAL"

                return {
                    "sentiment_score": round(max(-1.0, min(1.0, sentiment_score)), 4),
                    "sentiment_label": sentiment_label
                }
            except Exception as e:
                logger.debug(f"Transformer sentiment failed: {e}")

        # Fallback to VADER
        if self._vader is not None:
            try:
                scores = self._vader.polarity_scores(text)
                compound = scores["compound"]
                if compound >= 0.05:
                    sentiment_label = "POSITIVE"
                elif compound <= -0.05:
                    sentiment_label = "NEGATIVE"
                else:
                    sentiment_label = "NEUTRAL"
                return {
                    "sentiment_score": round(compound, 4),
                    "sentiment_label": sentiment_label
                }
            except Exception as e:
                logger.debug(f"VADER sentiment failed: {e}")

        # Final fallback: basic lexicon
        return self._basic_sentiment(text)

    def _basic_sentiment(self, text: str) -> Dict[str, Any]:
        """Basic lexicon-based sentiment as ultimate fallback."""
        positive_words = {
            "good", "great", "fresh", "excellent", "amazing", "perfect", "healthy",
            "tasty", "delicious", "organic", "quality", "best", "superb", "favorite",
            "fast", "cheap", "fair", "ripe", "sweet", "clean", "wonderful", "love",
            "recommend", "happy", "satisfied", "awesome", "fantastic", "brilliant"
        }
        negative_words = {
            "bad", "rotten", "wilted", "moldy", "stale", "damaged", "crushed", "sour",
            "spoiled", "overpriced", "expensive", "slow", "dirty", "mushy", "bruised",
            "unripe", "late", "small", "hard", "tough", "bitter", "awful", "terrible",
            "disappointed", "worst", "horrible", "poor", "unhappy", "regret"
        }

        words = set(re.findall(r'\b\w+\b', text.lower()))
        pos_count = len(words & positive_words)
        neg_count = len(words & negative_words)
        total = pos_count + neg_count

        if total == 0:
            return {"sentiment_score": 0.0, "sentiment_label": "NEUTRAL"}

        score = (pos_count - neg_count) / total
        if score > 0.15:
            label = "POSITIVE"
        elif score < -0.15:
            label = "NEGATIVE"
        else:
            label = "NEUTRAL"

        return {"sentiment_score": round(score, 4), "sentiment_label": label}

    def detect_intent(self, text: str) -> str:
        """Detect intent category from text.

        Returns one of: PRICE_INQUIRY, QUALITY_INQUIRY, DELIVERY_INQUIRY, GENERAL_CHAT
        """
        if not text or not str(text).strip():
            return "GENERAL_CHAT"

        text = str(text).strip().lower()

        # Try transformer zero-shot classification
        transformer = self._get_transformer_intent()
        if transformer is not None:
            try:
                candidate_labels = list(INTENT_KEYWORDS.keys())
                result = transformer(text, candidate_labels)
                top_label = result["labels"][0]
                top_score = result["scores"][0]
                if top_score > 0.5:
                    return top_label
            except Exception as e:
                logger.debug(f"Transformer intent failed: {e}")

        # Fallback: keyword matching
        scores = {intent: 0 for intent in INTENT_KEYWORDS}
        for intent, keywords in INTENT_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    scores[intent] += 1

        if max(scores.values()) > 0:
            return max(scores, key=scores.get)

        return "GENERAL_CHAT"

    def compute_semantic_similarity(self, text1: str, text2: str) -> float:
        """Calculate semantic similarity between two texts using TF-IDF or SpaCy vectors."""
        if not text1 or not text2:
            return 0.0

        # Try SpaCy vectors first
        if self._nlp is not None and self._nlp.has_vectors:
            try:
                doc1 = self._nlp(text1)
                doc2 = self._nlp(text2)
                if doc1.vector_norm > 0 and doc2.vector_norm > 0:
                    return float(doc1.similarity(doc2))
            except Exception as e:
                logger.debug(f"SpaCy similarity failed: {e}")

        # Fallback to TF-IDF
        if SKLEARN_AVAILABLE:
            try:
                corpus = [text1, text2]
                vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
                tfidf_matrix = vectorizer.fit_transform(corpus)
                similarity = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
                return float(similarity)
            except Exception as e:
                logger.debug(f"TF-IDF similarity failed: {e}")

        # Ultimate fallback: word overlap
        return self._word_overlap_similarity(text1, text2)

    def _word_overlap_similarity(self, text1: str, text2: str) -> float:
        """Simple Jaccard similarity on tokens."""
        tokens1 = set(re.findall(r'\b\w+\b', text1.lower()))
        tokens2 = set(re.findall(r'\b\w+\b', text2.lower()))

        if not tokens1 and not tokens2:
            return 1.0
        if not tokens1 or not tokens2:
            return 0.0

        intersection = tokens1 & tokens2
        union = tokens1 | tokens2
        return len(intersection) / len(union)

    def extract_keywords(self, text: str, top_n: int = 10) -> List[str]:
        """Extract top keywords from text."""
        if self._nlp is not None:
            try:
                doc = self._nlp(text)
                keywords = [
                    token.lemma_.lower()
                    for token in doc
                    if not token.is_stop and not token.is_punct and token.is_alpha
                ]
                # Count frequency
                from collections import Counter
                freq = Counter(keywords)
                return [word for word, _ in freq.most_common(top_n)]
            except Exception:
                pass

        # Fallback: simple frequency
        words = re.findall(r'\b\w+\b', text.lower())
        stopwords = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would", "can", "could", "should", "this", "that", "these", "those", "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them"}
        filtered = [w for w in words if w not in stopwords and len(w) > 2]
        from collections import Counter
        freq = Counter(filtered)
        return [word for word, _ in freq.most_common(top_n)]

    def preprocess_text(self, text: str) -> str:
        """Clean and normalize text."""
        if not text:
            return ""
        text = str(text).strip()
        text = re.sub(r'https?://\S+|www\.\S+', ' ', text)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.lower().strip()


# Global singleton instance
_nlp_engine: Optional[AgriNLPEngine] = None


def get_nlp_engine() -> AgriNLPEngine:
    """Get or create the global NLP engine instance."""
    global _nlp_engine
    if _nlp_engine is None:
        _nlp_engine = AgriNLPEngine()
    return _nlp_engine


# Convenience functions
def analyze_sentiment(text: str) -> Dict[str, Any]:
    return get_nlp_engine().analyze_sentiment(text)


def detect_intent(text: str) -> str:
    return get_nlp_engine().detect_intent(text)


def compute_semantic_similarity(text1: str, text2: str) -> float:
    return get_nlp_engine().compute_semantic_similarity(text1, text2)


def extract_keywords(text: str, top_n: int = 10) -> List[str]:
    return get_nlp_engine().extract_keywords(text, top_n)


if __name__ == "__main__":
    print("=" * 60)
    print("AgriNLPEngine Self-Test")
    print("=" * 60)

    engine = AgriNLPEngine()

    test_texts = [
        "How much per kg for the organic tomatoes?",
        "Are these mangoes naturally ripened and pesticide free?",
        "What is the delivery time to Mumbai?",
        "This is the best quality rice I have ever bought!",
        "Terrible service, received rotten vegetables.",
        "Okay product, nothing special but acceptable.",
        "Can I get a discount on bulk order?",
        "Is the wheat organic certified?",
    ]

    print("\n--- Sentiment Analysis ---")
    for text in test_texts:
        result = engine.analyze_sentiment(text)
        print(f"Text: {text[:50]}...")
        print(f"  Score: {result['sentiment_score']:.4f}, Label: {result['sentiment_label']}")

    print("\n--- Intent Detection ---")
    for text in test_texts:
        intent = engine.detect_intent(text)
        print(f"Text: {text[:50]}...")
        print(f"  Intent: {intent}")

    print("\n--- Semantic Similarity ---")
    pairs = [
        ("organic tomatoes price", "cost of organic tomatoes per kg"),
        ("fresh mangoes delivery", "shipping time for mangoes"),
        ("rice quality", "wheat variety"),
    ]
    for t1, t2 in pairs:
        sim = engine.compute_semantic_similarity(t1, t2)
        print(f"'{t1}' vs '{t2}' = {sim:.4f}")

    print("\n--- Keyword Extraction ---")
    sample = "Fresh organic tomatoes from our farm, pesticide free and naturally ripened"
    keywords = engine.extract_keywords(sample, top_n=5)
    print(f"Text: {sample}")
    print(f"Keywords: {keywords}")

    print("\n" + "=" * 60)
    print("Self-test completed successfully!")
    print("=" * 60)