"""NLP pipeline unit tests (run in the no-spaCy fallback path by default)."""

from nlp.processing import clean_text, embed_text, extract_keywords, semantic_scores, text_similarity, tokenize
from nlp.sentiment import SentimentAnalyzer, analyze_sentiment, classify_label


def test_clean_text_normalizes():
    raw = "<p>Fresh  ORGANIC </p> Tomatoes!! <a href='https://example.com'>link</a>"
    cleaned = clean_text(raw)
    assert cleaned == cleaned.lower()
    assert "<p>" not in cleaned
    assert "http" not in cleaned
    assert "!!" not in cleaned


def test_tokenize_removes_stopwords():
    tokens = tokenize("the fresh ripe tomatoes are delicious")
    assert "the" not in tokens
    assert "are" not in tokens
    assert tokens


def test_extract_keywords_returns_frequent_terms():
    text = "organic tomatoes fresh tomatoes ripe tomatoes and kale smoothies"
    keywords = extract_keywords(text, top_n=3)
    assert keywords
    assert "tomato" in keywords or "tomatoes" in keywords


def test_embed_text_returns_float_vector():
    vector = embed_text("fresh organic garlic")
    assert vector.size > 0
    assert (vector != 0).any()


def test_similar_texts_score_higher_than_unrelated():
    a = "fresh ripe organic tomatoes from the farm"
    b = "fresh ripe tomatoes"
    unrelated = "elephant galloping through the savanna"
    assert text_similarity(a, b) > text_similarity(a, unrelated)


def test_sentiment_positive():
    result = analyze_sentiment("The produce was absolutely fresh, delicious and amazing!")
    assert result["label"] == "positive"
    assert result["score"] > 0


def test_sentiment_negative():
    result = analyze_sentiment("The shipment was rotten, spoiled and terribly overpriced.")
    assert result["label"] == "negative"
    assert result["score"] < 0


def test_sentiment_neutral():
    result = analyze_sentiment("It arrived on time.")
    assert result["label"] == "neutral"
    assert result["score"] == 0.0


def test_analyzer_instance_round_trips():
    analyzer = SentimentAnalyzer()
    result = analyzer.analyze("fresh and tasty")
    assert classify_label(result["score"]) == result["label"]


def test_empty_sentiment_is_neutral():
    assert analyze_sentiment("") == {"label": "neutral", "score": 0.0, "magnitude": 0.0}


def test_semantic_scores_ranks_similar_first():
    docs = [
        "fresh organic tomatoes straight from the farm",
        "ripe red tomatoes ideal for cooking",
        "handwoven wool carpets and rugs",
    ]
    scores = semantic_scores("buy fresh tomatoes", docs)
    assert len(scores) == 3
    assert scores[0] > scores[2]
    assert scores[1] > scores[2]


def test_semantic_scores_empty_docs():
    assert semantic_scores("anything", []) == []