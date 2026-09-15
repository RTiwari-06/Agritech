"""Content-based product recommendations.

Products (and the search query) are embedded as TF-IDF feature vectors and
ranked with cosine similarity. This is deterministic, fast and requires no
external model downloads, while still honouring semantic terms shared between
a query and a listing.
"""

from typing import Iterable, List, Sequence, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def _get_attr_or_dict(obj, key: str, default=None):
    """Get *key* from *obj* whether it's a dict or an object with attributes."""
    if hasattr(obj, "get"):
        return obj.get(key, default)
    return getattr(obj, key, default)


class RecommendationEngine:
    """Fit a corpus of documents once, then query for similar items."""

    def __init__(
        self,
        max_features: int = 3000,
        ngram_range: Tuple[int, int] = (1, 2),
    ) -> None:
        self._vectorizer = TfidfVectorizer(
            stop_words="english", ngram_range=ngram_range, max_features=max_features
        )
        self._matrix: np.ndarray | None = None
        self._ids: List = []
        self._fitted: bool = False

    @property
    def is_fitted(self) -> bool:
        return self._fitted and self._matrix is not None

    def fit(self, documents: Sequence[str], ids: Sequence) -> "RecommendationEngine":
        """Build the TF-IDF matrix for ``documents`` aligned with ``ids``."""
        self._ids = list(ids)
        if documents:
            self._matrix = self._vectorizer.fit_transform(list(documents))
            self._fitted = True
        else:
            self._matrix = None
            self._fitted = False
        return self

    def recommend(self, query: str, top_k: int = 5) -> List[Tuple[any, float]]:
        """Rank fitted items against ``query``. Returns [(id, score), ...]."""
        if not self.is_fitted:
            return []
        query_vec = self._vectorizer.transform([query or ""])
        similarities = cosine_similarity(query_vec, self._matrix)[0]
        order = np.argsort(similarities)[::-1]
        results = []
        for index in order:
            score = float(similarities[index])
            if score <= 0:
                break
            results.append((self._ids[index], round(score, 4)))
            if len(results) >= top_k:
                break
        return results

    def similar(self, document_index: int, top_k: int = 5) -> List[Tuple[any, float]]:
        """Return the closest items to the item at ``document_index`` (excl. self)."""
        if not self.is_fitted or document_index >= self._matrix.shape[0]:
            return []
        similarities = cosine_similarity(self._matrix[document_index], self._matrix)[0]
        order = np.argsort(similarities)[::-1]
        results = []
        for index in order:
            if index == document_index or similarities[index] <= 0:
                continue
            results.append((self._ids[index], round(float(similarities[index]), 4)))
            if len(results) >= top_k:
                break
        return results

    @staticmethod
    def search_text_of(product) -> str:
        """Extract the searchable text from a Product (ORM object or MongoDB dict)."""
        if hasattr(product, "search_text"):
            return product.search_text()
        return " ".join(
            filter(None, [str(_get_attr_or_dict(product, "name", "")), str(_get_attr_or_dict(product, "description", ""))])
        )


def build_engine_from_products(products: Iterable) -> RecommendationEngine:
    """Fit a shared engine on a list of Product objects (ORM or MongoDB dicts)."""
    products = list(products)

    def _product_id(p):
        # MongoDB docs use ``_id``, ORM objects use ``id``.
        if hasattr(p, "get"):  # dict-like
            return p.get("_id") or p.get("id")
        return getattr(p, "id", None)

    ids = [_product_id(p) for p in products]
    documents = [
        RecommendationEngine.search_text_of(p) if hasattr(p, "search_text") else " ".join(
            filter(None, [str(_get_attr_or_dict(p, "name", "")), str(_get_attr_or_dict(p, "description", ""))])
        )
        for p in products
    ]
    return RecommendationEngine().fit(documents, ids)