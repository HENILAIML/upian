"""Unit and Integration Tests for Intelligent Text Model Routing (Phase 3)."""
import pytest
from router import classifier, heuristic_score, detect_capabilities
import llm


def test_heuristic_simple_prompt():
    score = heuristic_score("hi, how are you?")
    assert score < 0.35, f"Expected simple score < 0.35, got {score}"


def test_heuristic_medium_prompt():
    score = heuristic_score("Explain how HTTPS works with an example.")
    assert 0.15 <= score < 0.70, f"Expected medium score between 0.15 and 0.70, got {score}"


def test_heuristic_complex_prompt():
    prompt = (
        "Design a scalable microservices architecture for a global payments platform with "
        "idempotency, distributed transactions, 2PC, and write a Python thread-safe LRU cache with complexity analysis."
    )
    score = heuristic_score(prompt)
    assert score >= 0.70, f"Expected complex score >= 0.70, got {score}"


def test_capabilities_detection():
    code_prompt = "def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)"
    caps = detect_capabilities(code_prompt)
    assert "coding" in caps

    math_prompt = "Calculate the integral of e^(-x^2) dx and explain the probability density."
    caps = detect_capabilities(math_prompt)
    assert "math" in caps

    arch_prompt = "Compare microservices and monolithic architecture for high throughput distributed database."
    caps = detect_capabilities(arch_prompt)
    assert "system-design" in caps


def test_classifier_3_tier_routing():
    # Simple prompt
    res_simple = classifier.classify("What is the capital of France?")
    assert res_simple["tier"] in ("simple", "medium")
    assert "suggested_model" in res_simple
    assert res_simple["estimated_cost"] in ("free", "low")

    # Complex prompt
    res_complex = classifier.classify(
        "Design a fault-tolerant distributed consensus algorithm in Python, implement Leader Election with Raft, "
        "and prove safety under network partitions."
    )
    assert res_complex["tier"] == "complex"
    assert res_complex["legacy_tier"] == "strong"
    assert res_complex["score"] >= 0.70
    assert "coding" in res_complex["capabilities"] or "system-design" in res_complex["capabilities"]


def test_llm_build_chain_tiers():
    chain_simple = llm.build_chain("simple")
    assert len(chain_simple) > 0

    chain_medium = llm.build_chain("medium")
    assert len(chain_medium) > 0

    chain_complex = llm.build_chain("complex")
    assert len(chain_complex) > 0
    # Complex chain should include strong models or Gemini complex fallback
    model_names = [s["model"] for s in chain_complex]
    assert any("pro" in m.lower() or "strong" in m.lower() or "flash" in m.lower() for m in model_names)


def test_routing_user_preference():
    # User override to speed
    res_speed = classifier.classify("Design a huge distributed database", preference="speed")
    assert res_speed["tier"] == "simple"

    # User override to quality
    res_qual = classifier.classify("Hello", preference="quality")
    assert res_qual["tier"] == "complex"
