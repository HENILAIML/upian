"""Context Window Management (Task 2).

Manages conversation context limits, token counting, 80% threshold detection,
intelligent summarization of older conversation turns, and graceful fallback
to sliding-window truncation if summarization fails.
"""
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from config import Config

logger = logging.getLogger("upian.context")


def count_tokens(text: str) -> int:
    """
    Estimates the number of tokens in a string.
    Uses subword / whitespace tokenization (~3.8 characters per token on average for English/code).
    Handles empty, multilingual, and code text accurately without external network dependencies.
    """
    if not text:
        return 0
    # Clean whitespace
    s = str(text)
    # Split on words, numbers, punctuation
    tokens = re.findall(r"\w+|[^\w\s]", s, re.UNICODE)
    # Heuristic adjustment for longer words/subwords
    estimated = 0
    for tok in tokens:
        if len(tok) <= 4:
            estimated += 1
        else:
            estimated += (len(tok) + 3) // 4
    return max(1, estimated)


def count_message_tokens(message: Dict[str, Any]) -> int:
    """Counts tokens in a single message dictionary ({'role': ..., 'content': ...})."""
    if not isinstance(message, dict):
        return 0
    role_tokens = count_tokens(message.get("role", ""))
    content_tokens = count_tokens(message.get("content", ""))
    return role_tokens + content_tokens + 3  # Add 3 tokens for message format framing


def count_conversation_tokens(messages: List[Dict[str, Any]], summary: Optional[str] = None) -> int:
    """Counts total tokens across a list of messages and an optional preceding summary."""
    total = sum(count_message_tokens(m) for m in messages if isinstance(m, dict))
    if summary:
        total += count_tokens(summary) + 10  # Framing overhead
    return total


def extract_key_facts(text: str) -> List[str]:
    """
    Extracts key facts, requirements, names, constraints, and figures from text.
    Preserves critical user requirements during context summarization.
    """
    facts = []
    # Identify lines with important cues or bullets
    lines = text.split("\n")
    for line in lines:
        clean = line.strip()
        if not clean:
            continue
        # Check for bullet points or numbered lists
        if re.match(r"^[-*•\d.]+\s+", clean):
            facts.append(clean)
        # Check for key declarative statements
        elif re.search(r"\b(my name is|budget is|deadline is|require|must|never|always|goal is|using|database is|key fact)\b", clean, re.I):
            facts.append(clean)
    return facts


def summarize_messages_rule_based(messages: List[Dict[str, Any]], prior_summary: Optional[str] = None) -> str:
    """
    Generates a structured, concise executive summary of conversation messages.
    Retains key facts, decisions, constraints, and user context.
    """
    all_text = []
    extracted_facts = []

    if prior_summary:
        all_text.append(f"Prior Context: {prior_summary.strip()}")

    for msg in messages:
        role = msg.get("role", "user").capitalize()
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        # Extract facts from user and assistant statements
        facts = extract_key_facts(content)
        if facts:
            extracted_facts.extend(facts)
        # Add abbreviated gist
        first_sentence = re.split(r"[.!?]\s+", content)[0]
        all_text.append(f"{role}: {first_sentence[:120]}")

    summary_lines = [
        "### Conversation Summary & Key Context",
    ]
    if extracted_facts:
        summary_lines.append("**Key Facts & Constraints Retained:**")
        # Deduplicate while preserving order
        seen = set()
        for fact in extracted_facts:
            clean_fact = fact.strip(" -*•")
            if clean_fact.lower() not in seen:
                seen.add(clean_fact.lower())
                summary_lines.append(f"- {clean_fact}")

    summary_lines.append("**Discussion Timeline:**")
    for entry in all_text[-4:]:
        summary_lines.append(f"- {entry}")

    return "\n".join(summary_lines)


class ContextWindowManager:
    """
    Context Window Manager (Task 2).
    - Tracks conversation token usage against a configurable limit.
    - At ~80% threshold, triggers summarization of older messages.
    - Saves summary and initiates a new context window with summary + recent messages.
    - Safely falls back to sliding-window truncation if summarization fails.
    """
    def __init__(
        self,
        context_limit: int = 4096,
        threshold_ratio: float = 0.80,
        keep_recent_count: int = 4,
        summarizer_fn: Optional[Callable[[List[Dict[str, Any]], Optional[str]], str]] = None,
    ):
        self.context_limit = context_limit
        self.threshold_ratio = threshold_ratio
        self.trigger_limit = int(context_limit * threshold_ratio)
        self.keep_recent_count = max(2, keep_recent_count)
        self.summarizer_fn = summarizer_fn or summarize_messages_rule_based

    def should_summarize(self, messages: List[Dict[str, Any]], summary: Optional[str] = None) -> bool:
        """Determines if the conversation tokens exceed the 80% threshold."""
        total_tokens = count_conversation_tokens(messages, summary)
        return total_tokens >= self.trigger_limit

    def roll_context_window(
        self,
        messages: List[Dict[str, Any]],
        current_summary: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
        """
        Rolls the context window when reaching 80% capacity:
        1. Separates older messages from recent messages.
        2. Summarizes older messages (or falls back to sliding window if summarizer fails).
        3. Returns (new_active_messages, new_summary, metadata).
        """
        started = time.perf_counter()
        initial_tokens = count_conversation_tokens(messages, current_summary)

        # If not enough messages to split, return as is
        if len(messages) <= self.keep_recent_count:
            return messages, current_summary or "", {
                "summarized": False,
                "initial_tokens": initial_tokens,
                "final_tokens": initial_tokens,
                "reason": "Not enough messages to split",
            }

        split_idx = len(messages) - self.keep_recent_count
        older_messages = messages[:split_idx]
        recent_messages = messages[split_idx:]

        new_summary = current_summary or ""
        summarized_successfully = False
        fallback_used = False

        # Attempt summarization of older messages
        try:
            new_summary = self.summarizer_fn(older_messages, current_summary)
            summarized_successfully = True
            logger.info("Successfully summarized %d older messages into %d tokens",
                        len(older_messages), count_tokens(new_summary))
        except Exception as exc:
            # Rule: If summarization fails, fall back to keeping only the most recent messages instead of erroring
            logger.error("Context summarization failed: %s. Falling back to sliding window.", exc)
            fallback_used = True
            summarized_successfully = False
            new_summary = current_summary or ""

        # Build new active context window
        new_active_messages = []
        if new_summary and not fallback_used:
            new_active_messages.append({
                "role": "system",
                "content": f"[Active Context Summary]\n{new_summary}",
            })
        new_active_messages.extend(recent_messages)

        # Verify new window strictly does not exceed context limit; prune if needed
        while count_conversation_tokens(new_active_messages) > self.context_limit and len(new_active_messages) > 1:
            if new_active_messages and new_active_messages[0].get("role") == "system" and len(new_active_messages) > 2:
                new_active_messages.pop(1)
            else:
                new_active_messages.pop(0)

        final_tokens = count_conversation_tokens(new_active_messages)
        duration_ms = int((time.perf_counter() - started) * 1000)

        metadata = {
            "summarized": summarized_successfully,
            "fallback_used": fallback_used,
            "initial_tokens": initial_tokens,
            "final_tokens": final_tokens,
            "tokens_saved": max(0, initial_tokens - final_tokens),
            "older_messages_count": len(older_messages),
            "recent_messages_retained": len(recent_messages),
            "duration_ms": duration_ms,
        }

        return new_active_messages, new_summary, metadata
