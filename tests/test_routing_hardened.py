"""Hardened Test Suite for Query Classification & Model Routing (Task 1).
Tests rule-based classification behind an interface:
- cheap vs expensive queries
- borderline queries
- empty & whitespace queries
- very long queries
- non-English queries
- failure fallback to cheap model
"""
import pytest
from router import QueryClassifierInterface, RuleBasedClassifier, rule_based_classifier
from config import Config


def test_classifier_implements_interface():
    assert issubclass(RuleBasedClassifier, QueryClassifierInterface)
    classifier = RuleBasedClassifier()
    assert isinstance(classifier, QueryClassifierInterface)


def test_cheap_simple_queries():
    classifier = RuleBasedClassifier()
    cheap_examples = [
        "hi",
        "hello there",
        "thanks a lot",
        "what is the capital of France?",
        "Who wrote Hamlet?",
        "What day is today?",
        "Define gravity in one sentence",
    ]
    for q in cheap_examples:
        res = classifier.classify(q)
        assert res["tier"] in ("cheap", "simple"), f"Query '{q}' should be cheap, got {res['tier']}"
        assert res["score"] < classifier.threshold, f"Query '{q}' score {res['score']} should be below threshold"
        assert res["model"] == classifier.cheap_model
        assert "classify_ms" in res
        assert "reason" in res


def test_expensive_complex_queries():
    classifier = RuleBasedClassifier()
    expensive_examples = [
        "Write a Python implementation of a thread-safe LRU cache with tests and explain the time complexity.",
        "Design a scalable microservices architecture for a payments platform with idempotency, retries and eventual consistency.",
        "Debug this stack trace: my Django app deadlocks in PostgreSQL under concurrent writes. Find the root cause.",
        "Prove that the square root of 2 is irrational and derive the mathematical steps.",
        "Optimize this SQL query joining 5 large tables and write an execution plan with indexing recommendations.",
    ]
    for q in expensive_examples:
        res = classifier.classify(q)
        assert res["tier"] in ("expensive", "complex"), f"Query '{q}' should be expensive, got {res['tier']}"
        assert res["score"] >= classifier.threshold, f"Query '{q}' score {res['score']} should be >= threshold"
        assert res["model"] == classifier.expensive_model
        assert res["legacy_tier"] == "strong"


def test_borderline_queries():
    classifier = RuleBasedClassifier(threshold=0.5)
    # Borderline query with moderate explanatory content
    query = "Can you explain the main difference between REST and GraphQL APIs with a short comparison?"
    res = classifier.classify(query)
    assert 0.20 <= res["score"] <= 0.70
    assert res["tier"] in ("cheap", "expensive")
    assert res["model"] in (classifier.cheap_model, classifier.expensive_model)


def test_empty_and_whitespace_queries():
    classifier = RuleBasedClassifier()
    for empty_q in ["", "   ", "\t\n", None]:
        res = classifier.classify(empty_q)
        assert res["tier"] == "cheap"
        assert res["score"] == 0.0
        assert res["model"] == classifier.cheap_model
        assert res["task_type"] == "empty"


def test_very_long_queries():
    classifier = RuleBasedClassifier()
    # 5,000 character document summary query
    long_text = "The quick brown fox jumps over the lazy dog. " * 115
    long_query = f"Please read and summarize this entire transcript:\n\n{long_text}"
    assert len(long_query) > 5000

    res = classifier.classify(long_query)
    assert "tier" in res
    assert res["classify_ms"] > 0
    # Long text receives higher complexity weight
    assert res["score"] >= 0.35


def test_non_english_queries():
    classifier = RuleBasedClassifier()
    non_english_examples = [
        # Simple Spanish greeting
        ("Hola, buenos días, ¿cómo estás?", "cheap"),
        # Simple Hindi greeting
        ("नमस्ते, आप कैसे हैं?", "cheap"),
        # Simple French question
        ("Quelle est la capitale de l'Espagne?", "cheap"),
        # Complex French coding prompt
        ("Écrivez un script Python pour implémenter un algorithme de tri rapide avec des tests unitaires.", "expensive"),
        # Complex Spanish architecture prompt
        ("Diseñe una arquitectura de microservicios distribuida y escalable con idempotencia y tolerancia a fallos.", "expensive"),
    ]
    for text, expected_tier in non_english_examples:
        res = classifier.classify(text)
        assert res["tier"] == expected_tier, f"Query '{text}' expected {expected_tier}, got {res['tier']}"


def test_classifier_failure_fallback_to_cheap(monkeypatch):
    classifier = RuleBasedClassifier()

    # Force a failure inside the scoring mechanism
    def broken_heuristic(prompt):
        raise RuntimeError("Unexpected classifier runtime failure simulation")

    import router
    monkeypatch.setattr(router, "heuristic_score", broken_heuristic)

    # Must NOT crash; must default safely to cheap model and log failure
    res = classifier.classify("Any sample query")
    assert res["tier"] == "cheap"
    assert res["model"] == classifier.cheap_model
    assert "fallback" in res["method"] or "Fallback" in res["reason"]
