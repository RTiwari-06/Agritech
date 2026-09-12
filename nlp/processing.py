"""Semantic text processing.

Uses spaCy (``en_core_web_sm`` by default) for tokenisation, lemmatisation and
word vectors when available, and degrades to a deterministic regular-expression
+ hashed bag-of-words pipeline otherwise so the package always runs.
"""

import hashlib
import html
import re
import subprocess
import sys
from collections import Counter
from typing import Any, List

import numpy as np

from config import Config

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")
NON_ALPHA_RE = re.compile(r"[^a-z0-9\s]")
SPACE_RE = re.compile(r"\s+")

STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "else", "of", "to",
        "in", "on", "at", "for", "with", "by", "from", "up", "about", "into",
        "over", "after", "before", "under", "between", "out", "off", "is", "are",
        "was", "were", "be", "been", "being", "am", "has", "have", "had", "do",
        "does", "did", "will", "would", "can", "could", "should", "shall", "may",
        "might", "must", "i", "you", "he", "she", "it", "we", "they", "them",
        "his", "her", "its", "our", "your", "their", "this", "that", "these",
        "those", "also", "not", "no", "so", "very", "just", "only", "more",
        "most", "such", "each", "which", "who", "whom", "what", "when", "where",
        "why", "how", "too", "all", "own",
    }
)

_EMBED_DIM = 256
_nlp: Any = None


def _load_spacy():
    """Lazily load the spaCy model once; None if unavailable."""
    global _nlp
    if _nlp is None:
        try:
            import spacy

            try:
                _nlp = spacy.load(Config.NLP_MODEL)
            except OSError:
                if Config.AUTO_DOWNLOAD_NLP_MODEL:
                    subprocess.check_call(
                        [sys.executable, "-m", "spacy", "download", Config.NLP_MODEL]
                    )
                    _nlp = spacy.load(Config.NLP_MODEL)
                else:
                    _nlp = None
        except ImportError:
            _nlp = None
    return _nlp


def clean_text(text: str) -> str:
    """Normalise free-text: strip tags/URLs/punctuation, lowercase."""
    if not text:
        return ""
    text = html.unescape(str(text)).lower()
    text = URL_RE.sub(" ", text)
    text = HTML_TAG_RE.sub(" ", text)
    text = NON_ALPHA_RE.sub(" ", text)
    return SPACE_RE.sub(" ", text).strip()


def tokenize(text: str) -> List[str]:
    """Split text into lowercase tokens, skipping stopwords."""
    nlp = _load_spacy()
    if nlp is not None:
        doc = nlp(clean_text(text))
        return [
            token.lower_
            for token in doc
            if not token.is_punct and not token.is_space and not token.is_stop
        ]
    return [w for w in clean_text(text).split() if w not in STOPWORDS]


def lemmatize(text: str) -> List[str]:
    """Reduce tokens to base forms (spaCy lemmas, or plain tokens as fallback)."""
    nlp = _load_spacy()
    if nlp is not None:
        doc = nlp(clean_text(text))
        return [
            token.lemma_.lower()
            for token in doc
            if not token.is_punct
            and not token.is_space
            and not token.is_stop
            and token.lemma_.lower() not in STOPWORDS
        ]
    return [w for w in clean_text(text).split() if w not in STOPWORDS]


def extract_keywords(text: str, top_n: int = 8) -> List[str]:
    """Return the most frequent meaningful terms in the text."""
    counter = Counter(lemmatize(text))
    return [word for word, _ in counter.most_common(top_n)]


def _stable_hash(token: str) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def embed_text(text: str) -> np.ndarray:
    """Return a fixed-size embedding for the given text.

    Prefers spaCy word vectors; falls back to a hashed bag-of-words embedding
    so callers always receive a dense numpy vector.
    """
    nlp = _load_spacy()
    if nlp is not None and nlp.has_vectors:
        vector = nlp(clean_text(text)).vector
        if np.linalg.norm(vector) > 0:
            return vector.copy()

    vector = np.zeros(_EMBED_DIM, dtype=np.float64)
    for token in lemmatize(text):
        vector[_stable_hash(token) % _EMBED_DIM] += 1.0
    norm = np.linalg.norm(vector)
    if norm > 0:
        vector = vector / norm
    return vector


def text_similarity(a: str, b: str) -> float:
    """Cosine similarity in [0, 1] between two pieces of text."""
    vec_a, vec_b = embed_text(a), embed_text(b)
    denom = np.linalg.norm(vec_a) * np.linalg.norm(vec_b)
    if denom == 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / denom)