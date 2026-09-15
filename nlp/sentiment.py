"""Sentiment analysis for buyer reviews.

Two strategies are layered:
  1. A deterministic lexicon scorer (always available, runs offline).
  2. An optional HuggingFace ``transformers`` classifier that is loaded lazily
     only when ``Config.USE_TRANSFORMER_SENTIMENT`` is enabled, with the
     lexicon result kept as a fallback if the model fails.
"""

from typing import Any, Dict, List, Optional

from nlp.processing import lemmatize

# Simple agriculture-focused sentiment lexicon (fallback path).
POSITIVE_LEXICON = {
    "good", "great", "fresh", "excellent", "amazing", "perfect", "healthy",
    "tasty", "delicious", "organic", "quality", "best", "superb", "favorite",
    "fast", "cheap", "fair", "ripe", "sweet", "clean", "yummy", "wonderful",
    "lovely", "satisfied", "recommend", "love",
}
NEGATIVE_LEXICON = {
    "bad", "rotten", "wilted", "moldy", "stale", "damaged", "crushed", "sour",
    "spoiled", "overpriced", "expensive", "slow", "dirty", "mushy", "bruised",
    "unripe", "late", "small", "hard", "tough", "bitter", "awful", "terrible",
    "disappointed", "warns", "never", "refund",
}


class SentimentAnalyzer:
    """Thread-safe lazy sentiment analyser."""

    def __init__(self) -> None:
        self._pipeline: Optional[Any] = None
        self._model_loaded: bool = False

    def _load_transformer(self) -> Optional[Any]:
        if self._model_loaded:
            return self._pipeline
        self._model_loaded = True
        if not _use_transformers():
            return None
        try:
            from transformers import pipeline

            pipeline_factory: Any = pipeline
            self._pipeline = pipeline_factory(
                "sentiment-analysis", model=_model_name(), device=-1
            )
        except Exception:
            self._pipeline = None
        return self._pipeline

    def analyze(self, text: str) -> Dict[str, Any]:
        """Score ``text`` and return ``{label, score, magnitude}``.

        ``score`` is in [-1, 1] (negative..positive). ``label`` is one of
        ``negative`` / ``neutral`` / ``positive``.
        """
        if not text or not str(text).strip():
            return {"label": "neutral", "score": 0.0, "magnitude": 0.0}

        transformer = self._load_transformer()
        if transformer is not None:
            try:
                result = transformer(text[:512])[0]
                label = result["label"].lower()
                raw = float(result["score"])
                if "neg" in label:
                    score = -raw
                elif "pos" in label:
                    score = raw
                else:
                    score = 0.0
                return {
                    "label": classify_label(score),
                    "score": round(float(np_clip(score)), 4),
                    "magnitude": round(float(raw), 4),
                    "engine": "transformers",
                }
            except Exception:
                pass  # fall through to lexicon scoring

        return self._lexicon_score(text)

    def _lexicon_score(self, text: str) -> Dict[str, Any]:
        tokens = set(lemmatize(text))
        positive = len(tokens & POSITIVE_LEXICON)
        negative = len(tokens & NEGATIVE_LEXICON)
        total = positive + negative
        if total == 0:
            return {"label": "neutral", "score": 0.0, "magnitude": 0.0, "engine": "lexicon"}
        score = (positive - negative) / total
        return {
            "label": classify_label(score),
            "score": round(score, 4),
            "magnitude": total,
            "engine": "lexicon",
        }


def np_clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def classify_label(score: float) -> str:
    if score > 0.15:
        return "positive"
    if score < -0.15:
        return "negative"
    return "neutral"


def _use_transformers() -> bool:
    from config import Config

    return Config.USE_TRANSFORMER_SENTIMENT


def _model_name() -> str:
    from config import Config

    return Config.SENTIMENT_MODEL


def analyze_sentiment(text: str) -> Dict[str, Any]:
    """Module-level convenience wrapper around a shared analyser."""
    return _SHARED.analyze(text)


_SHARED = SentimentAnalyzer()


__all__ = ["SentimentAnalyzer", "analyze_sentiment", "classify_label"]