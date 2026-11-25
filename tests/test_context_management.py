"""Unit & Integration Tests for Context Window Management (Task 2)."""
import pytest
from context_manager import (
    ContextWindowManager,
    count_conversation_tokens,
    count_tokens,
    extract_key_facts,
    summarize_messages_rule_based,
)


def test_token_counting():
    text = "Hello world! This is a simple test sentence."
    tokens = count_tokens(text)
    assert tokens > 0
    assert 6 <= tokens <= 15

    empty_tokens = count_tokens("")
    assert empty_tokens == 0

    conversation = [
        {"role": "user", "content": "Hello, how are you today?"},
        {"role": "assistant", "content": "I am an AI assistant, ready to help you."},
    ]
    conv_tokens = count_conversation_tokens(conversation)
    assert conv_tokens > count_tokens("Hello, how are you today?")


def test_80_percent_threshold_detection():
    # Set limit to 100 tokens -> 80% threshold = 80 tokens
    manager = ContextWindowManager(context_limit=100, threshold_ratio=0.80)
    assert manager.trigger_limit == 80

    short_conversation = [
        {"role": "user", "content": "Hi there"},
        {"role": "assistant", "content": "Hello!"},
    ]
    assert not manager.should_summarize(short_conversation)

    # Long conversation exceeding 80 tokens
    long_conversation = [
        {"role": "user", "content": "Explain quantum computing in detail: " + ("qubits superposition entanglement " * 10)},
        {"role": "assistant", "content": "Quantum computing uses quantum mechanics: " + ("circuits algorithms speedup " * 10)},
    ]
    assert manager.should_summarize(long_conversation)


def test_key_facts_extraction_and_retention():
    text = (
        "Project Requirements:\n"
        "- My name is Alice Vance\n"
        "- The project budget is $50,000 USD\n"
        "- Database is PostgreSQL with strict ACID requirements\n"
        "- Deadline is December 31, 2026\n"
        "We also had some casual conversation here."
    )
    facts = extract_key_facts(text)
    assert len(facts) >= 4
    assert any("Alice Vance" in f for f in facts)
    assert any("$50,000" in f for f in facts)
    assert any("PostgreSQL" in f for f in facts)
    assert any("December 31" in f for f in facts)


def test_long_conversation_rollover_and_fact_survival():
    manager = ContextWindowManager(context_limit=250, threshold_ratio=0.80, keep_recent_count=2)

    messages = [
        {"role": "user", "content": "My name is Bob. I am building an automated payment gateway. My budget is $25,000."},
        {"role": "assistant", "content": "Nice to meet you Bob. I will help design the payment gateway within the $25,000 budget."},
        {"role": "user", "content": "Key fact: The database must be PostgreSQL and all webhooks must be strictly idempotent."},
        {"role": "assistant", "content": "Understood. We will use PostgreSQL with unique constraint indexes on transaction IDs."},
        {"role": "user", "content": "Here is more discussion: " + ("filler discussion details about styling " * 15)},
        {"role": "assistant", "content": "Response to styling: " + ("more design details and CSS considerations " * 15)},
        {"role": "user", "content": "Now what is the next step for deployment?"},
        {"role": "assistant", "content": "The next step is setting up Docker containers and running health checks."},
    ]

    assert manager.should_summarize(messages)

    new_messages, summary, meta = manager.roll_context_window(messages)

    # 1. Verification: Metadata indicates successful summarization
    assert meta["summarized"] is True
    assert meta["fallback_used"] is False
    assert meta["tokens_saved"] > 0
    assert meta["final_tokens"] < meta["initial_tokens"]
    assert meta["final_tokens"] <= manager.context_limit

    # 2. Verification: Key facts survived into the summary
    assert "Bob" in summary
    assert "$25,000" in summary
    assert "PostgreSQL" in summary or "idempotent" in summary

    # 3. Verification: Most recent 2 messages are retained
    assert new_messages[-2]["content"] == messages[-2]["content"]
    assert new_messages[-1]["content"] == messages[-1]["content"]

    # 4. Verification: System message contains summary
    assert new_messages[0]["role"] == "system"
    assert "Active Context Summary" in new_messages[0]["content"]


def test_fallback_to_recent_on_summarization_failure():
    # Summarizer that raises an unexpected exception
    def failing_summarizer(older, current):
        raise RuntimeError("Summarization service unavailable or timed out")

    manager = ContextWindowManager(
        context_limit=200,
        threshold_ratio=0.80,
        keep_recent_count=2,
        summarizer_fn=failing_summarizer,
    )

    messages = [
        {"role": "user", "content": "Older message 1: " + ("content " * 15)},
        {"role": "assistant", "content": "Older message 2: " + ("content " * 15)},
        {"role": "user", "content": "Older message 3: " + ("content " * 15)},
        {"role": "assistant", "content": "Older message 4: " + ("content " * 15)},
        {"role": "user", "content": "Recent question: Can we proceed?"},
        {"role": "assistant", "content": "Recent answer: Yes, proceeding now."},
    ]

    # Must NOT raise exception; must safely fall back
    new_messages, summary, meta = manager.roll_context_window(messages)

    assert meta["fallback_used"] is True
    assert meta["summarized"] is False
    # Verifies only recent messages are kept
    assert len(new_messages) == 2
    assert new_messages[0]["content"] == "Recent question: Can we proceed?"
    assert new_messages[1]["content"] == "Recent answer: Yes, proceeding now."
