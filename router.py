"""Prompt classifier: Qwen3-Embedding similarity + multi-feature heuristics.
Routes prompts into 3 complexity tiers: 'simple', 'medium', 'complex'
(with backwards-compatible legacy_tier mapping to 'weak' / 'strong')."""
import abc
import logging
import math
import os
import re
import threading
import time

logger = logging.getLogger("upian.router")

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from config import Config, SLOTS


class QueryClassifierInterface(abc.ABC):
    """
    Abstract interface for query classification and model routing.
    Allows rule-based heuristics or learned ML/embedding models to be used interchangeably.
    """
    @abc.abstractmethod
    def classify(self, query: str, **kwargs) -> dict:
        """
        Classifies a query into cheap or expensive routing decision.
        Returns a dict containing:
          - tier: 'cheap' (simple) or 'expensive' (complex)
          - score: float (0.0 to 1.0)
          - model: selected model name
          - task_type: detected task type
          - reason: explanation of routing decision
        """
        pass


class RuleBasedClassifier(QueryClassifierInterface):
    """
    Rule-based query classifier that evaluates:
    1. Query length (character and word counts)
    2. Keywords (code, math, architecture, analysis vs greetings/simple facts)
    3. Task type (coding, math, system_design, analysis, translation, image_request, greeting, factual)
    4. Image requested detection

    Routes queries to 'cheap' (simple model) or 'expensive' (complex model).
    If classification fails for any reason, safely defaults to the cheap model and logs the failure.
    """
    def __init__(self, cheap_model=None, expensive_model=None, threshold=None):
        self._cheap_model = cheap_model
        self._expensive_model = expensive_model
        self._threshold = threshold

    @property
    def cheap_model(self):
        return self._cheap_model or getattr(Config, "CHEAP_MODEL_NAME", None) or getattr(Config, "GEMINI_MEDIUM_MODEL", "gemini-3.8-flash")

    @property
    def expensive_model(self):
        return self._expensive_model or getattr(Config, "EXPENSIVE_MODEL_NAME", None) or getattr(Config, "GEMINI_COMPLEX_MODEL", "gemini-3.8-pro")

    @property
    def threshold(self):
        return self._threshold if self._threshold is not None else getattr(Config, "CLASSIFIER_THRESHOLD", 0.5)

    def classify(self, query: str, **kwargs) -> dict:
        started = time.perf_counter()
        try:
            if not isinstance(query, str):
                query = str(query or "")

            raw_query = query.strip()
            if not raw_query:
                res = {
                    "tier": "cheap",
                    "legacy_tier": "weak",
                    "score": 0.0,
                    "model": self.cheap_model,
                    "suggested_model": self.cheap_model,
                    "task_type": "empty",
                    "capabilities": ["general"],
                    "classify_ms": max(1, int((time.perf_counter() - started) * 1000)),
                    "reason": "Empty or whitespace query defaulted to cheap model",
                    "method": "rule_based",
                }
                logger.info("Routing decision: %s", res)
                return res

            # 1. Check if an image generation/editing task is requested
            is_img = bool(re.search(
                r"\b(generate|create|make|draw|render|paint|sketch)\s+(an?\s+)?(image|picture|photo|illustration|graphic|art)\b|"
                r"\b(image\s+of|photo\s+of|picture\s+of)\b",
                raw_query,
                re.I,
            ))

            # 2. Detect capabilities / task type
            caps = detect_capabilities(raw_query)
            if is_img:
                task_type = "image_request"
            elif "coding" in caps:
                task_type = "coding"
            elif "system-design" in caps:
                task_type = "system_design"
            elif "deep-reasoning" in caps:
                task_type = "analysis"
            elif "math" in caps:
                task_type = "math"
            elif "translation" in caps:
                task_type = "translation"
            elif _GREET.match(raw_query) and len(raw_query.split()) <= 8:
                task_type = "greeting"
            elif len(raw_query.split()) < 15 and re.match(r"^\s*(what|who|when|where|why|which|define|how many)\b", raw_query, re.I):
                task_type = "factual"
            else:
                task_type = "general_knowledge"

            # 3. Compute score using rule-based heuristics
            score = heuristic_score(raw_query)

            # Override by preference if specified
            pref = (kwargs.get("preference") or getattr(Config, "ROUTING_PREFERENCE", "auto")).lower()
            if pref in ("speed", "fast", "low_cost"):
                tier = "cheap"
            elif pref in ("quality", "pro", "premium"):
                tier = "expensive"
            else:
                tier = "expensive" if score >= self.threshold else "cheap"

            selected_model = self.expensive_model if tier == "expensive" else self.cheap_model
            legacy_tier = "strong" if tier == "expensive" else "weak"
            reason = f"Task: {task_type}, score: {score:.3f} vs threshold: {self.threshold}"

            res = {
                "tier": tier,
                "legacy_tier": legacy_tier,
                "score": round(score, 3),
                "model": selected_model,
                "suggested_model": selected_model,
                "task_type": task_type,
                "capabilities": caps,
                "is_image_request": is_img,
                "classify_ms": max(1, int((time.perf_counter() - started) * 1000)),
                "reason": reason,
                "method": "rule_based",
            }
            logger.info(
                "Routing decision: query=%r (len=%d) -> tier=%s, model=%s, score=%.3f, reason=%s",
                raw_query[:50], len(raw_query), tier, selected_model, score, reason
            )
            return res

        except Exception as exc:
            # Rule: If classification fails, default to the cheap model and log the failure.
            logger.error(
                "Query classification failed for query %r: %s. Defaulting to cheap model.",
                str(query)[:50], exc
            )
            return {
                "tier": "cheap",
                "legacy_tier": "weak",
                "score": 0.0,
                "model": self.cheap_model,
                "suggested_model": self.cheap_model,
                "task_type": "fallback_error",
                "capabilities": ["general"],
                "classify_ms": max(1, int((time.perf_counter() - started) * 1000)),
                "reason": f"Fallback to cheap model due to error: {exc}",
                "method": "fallback",
            }

HARD_EXAMPLES = [
    "Design a scalable microservices architecture for a payments platform with idempotency, retries and eventual consistency.",
    "Write a Python implementation of a thread-safe LRU cache and explain the time complexity of each operation.",
    "Debug this stack trace: my Django app deadlocks in PostgreSQL under concurrent writes. Find the root cause.",
    "Prove that the square root of 2 is irrational and then generalize the argument.",
    "Analyze the trade-offs between event sourcing and CRUD for a financial ledger and give a recommendation.",
    "Derive the gradient of the cross-entropy loss with softmax and implement backpropagation from scratch.",
    "Refactor this 300-line function into clean modules with unit tests and explain each decision.",
    "Create a detailed 12-month go-to-market strategy for a B2B SaaS startup with a budget breakdown.",
    "Compare transformer and state-space architectures for long-context modelling, including failure modes.",
    "Optimize this SQL query that joins five large tables and propose indexes and a rewritten execution plan.",
    "Review the liability clauses in this contract and highlight the legal risks for the buyer.",
    "Solve this multi-step probability problem and show every step of the reasoning.",
]

MEDIUM_EXAMPLES = [
    "Explain how HTTPS works with an example.",
    "Summarize the main themes of Hamlet in three paragraphs.",
    "Write a professional email asking for a deadline extension.",
    "What are the differences between SQL and NoSQL databases?",
    "How do solar panels generate electricity?",
    "Translate this paragraph into French and explain any idiomatic expressions.",
    "Explain the difference between a process and a thread.",
    "Provide a recipe for authentic Neapolitan pizza with dough fermentation instructions.",
]

EASY_EXAMPLES = [
    "hi, how are you?",
    "What is the capital of France?",
    "Translate 'good morning' to Spanish.",
    "Give me a synonym for happy.",
    "What is 15 plus 27?",
    "Tell me a short joke.",
    "Who wrote Romeo and Juliet?",
    "Suggest a name for my cat.",
    "What day comes after Friday?",
    "Summarize this in a few words: the weather is nice today.",
    "How do you spell necessary?",
    "Thanks, that helps!",
]

_HARD = re.compile(
    r"\b(prove|proof|derive|derivation|optimi[sz]e|architect\w*|arquitect\w*|system design|algorith[me]?\w*|"
    r"complexity|refactor|debug|stack ?trace|impl[ée]ment\w*|regression|theorem|integral|differential|"
    r"step[- ]by[- ]step|analy[sz]e|analysis|trade-?offs?|kubernetes|dockerfile|sql|concurren\w+|"
    r"race condition|deadlock|security|vulnerab\w+|legal|contract|diagnos\w+|strategy|business plan|"
    r"scalab\w+|distribu\w+|microserv\w+|benchmark|from scratch|idempotenc\w+|consensus|raft|paxos|"
    r"multithread\w+|thread-safe|lru cache|database index|shard\w+|partition\w+|square root|irrational\w*|"
    r"strong prompt|complex prompt|advanced prompt|reasoning prompt|in-depth|deep dive|step-by-step reasoning|architecture design|"
    r"rag\b|retrieval[- ]augmented|vector\s*db|vector\s*database|embeddings?|semantic search|pip[e]?lines?)\b",
    re.I,
)
_MEDIUM = re.compile(
    r"\b(explain|difference|compare|comparison|guide|overview|summary|summarize|"
    r"example|examples|how does|how to|benefits|tradeoffs?|pros and cons|tutorial|"
    r"https?|protocol|rest api|graphql|docker|git|database|pipeline|microservice)\b",
    re.I,
)
_CODE = re.compile(
    r"```|\bdef \w+\(|\bclass \w+|\bfunction\s*\w*\(|#include|\bimport \w+|=>|\bSELECT\b.+\bFROM\b|"
    r"\bfor\s*\(.+;\s*.+;\s*.+\)|\bconst \w+\s*=|\blet \w+\s*=|\b(script|code)\s+python\b|"
    r"\btests?\s+unitaires?\b",
    re.I | re.S,
)
_GREET = re.compile(
    r"^\s*(hi|hello|hey|yo|thanks|thank you|ok|okay|good (morning|evening|night)|namaste|kem cho|hola|bonjour)\b",
    re.I,
)
_MATH = re.compile(
    r"\b(solve|integral|differential|matrix|eigenvalue|derivative|equation|probability|"
    r"distribution|standard deviation|permutation|combination|square root|irrational\w*|math\w*)\b",
    re.I,
)


def detect_capabilities(prompt):
    caps = []
    is_coding = bool(
        _CODE.search(prompt)
        or re.search(r"\b(code|cod+ing|program\w*|develop\w*|python|javascript|typescript|c\+\+|rust|java|html|css|sql|regex|script|flask|fastapi|django|api\b|rest api|endpoint|pipeline|rag\b|કોડ|પાયથન|પ્રોગ્રામ)\b", prompt, re.I)
        or (
            re.search(r"\b(lakho|lakhi|lakhva|lakhvanu|banavo|aapo|લખો|બનાવો|આપો)\b", prompt, re.I)
            and re.search(r"\b(code|python|program|script|loop|prinet|print|function|app|કોડ|પાયથન|પ્રિન્ટ)\b", prompt, re.I)
        )
    )
    if is_coding:
        caps.append("coding")
    if _MATH.search(prompt) or re.search(r"\b(ganatari|hisab|સરવાળો|બાદબાકી|ગુણાકાર|ભાગાકાર)\b", prompt, re.I):
        caps.append("math")
    if re.search(r"\b(architect\w*|arquitect\w*|microserv\w*|distribu\w*|system design|database|concurrency)\b", prompt, re.I):
        caps.append("system-design")
    if re.search(r"\b(analy[sz]e|compare|trade-?offs?|critique|evaluate|review|strong prompt|complex prompt|advanced prompt|reasoning prompt|deep reasoning)\b", prompt, re.I):
        caps.append("deep-reasoning")
    if re.search(r"\b(translate|french|spanish|german|hindi|gujarati|gujlish|ગુજરાતી|japanese|અનુવાદ|ભાષાંતર)\b", prompt, re.I):
        caps.append("translation")
    return caps or ["general-knowledge"]


def heuristic_score(prompt):
    words = len(prompt.split())
    chars = len(prompt)

    # Fast drop for greetings and pleasantries
    if _GREET.match(prompt) and words <= 8:
        return 0.0

    score = 0.10

    # Explicit strong / complex prompt indicator bonus
    if re.search(r"\b(strong\s+prompt|complex\s+prompt|advanced\s+prompt|reasoning\s+prompt)\b", prompt, re.I):
        score += 0.45

    # Length-based scaling
    if words > 150 or chars > 900:
        score += 0.35
    elif words > 60 or chars > 350:
        score += 0.22
    elif words >= 15:
        score += 0.10

    # Code structures or explicit code requests
    if _CODE.search(prompt):
        score += 0.35
    elif re.search(r"\b((write|wirte|build|create|lakho|lakhi|lakhva|karo|લખો|બનાવો)\s+(a\s+)?(code|coding|cod+ing|python|script|program|function|pipeline|api|app|class)|(code|python|loop|prinet|print|પાયથન|કોડ)\s+.*(lakho|lakhi|lakhva|karo|લખો|બનાવો)|implement|implémente|code\s+a|write\s+a\s+script|écrivez|ecrivez|escriba|script\s+python|tests?\s+unitaires?)\b", prompt, re.I):
        score += 0.25

    # Medium explanatory prompts
    if _MEDIUM.search(prompt):
        score += 0.25

    # Hard / complex domain terms
    hard_matches = len(_HARD.findall(prompt))
    if hard_matches > 0:
        score += min(0.50, 0.15 * hard_matches)

    # Math concepts
    math_matches = len(_MATH.findall(prompt))
    if math_matches > 0:
        score += min(0.25, 0.10 * math_matches)

    # Multi-domain capability bonus
    caps = detect_capabilities(prompt)
    if len(caps) >= 2 and ("system-design" in caps or "coding" in caps or "deep-reasoning" in caps):
        score += 0.15

    if prompt.count("?") >= 3:
        score += 0.08

    return max(0.0, min(1.0, score))


class Classifier:
    def __init__(self):
        self._load_lock = threading.Lock()
        self._enc_lock = threading.Lock()
        self._tried = False
        self._model = None
        self._hard = None
        self._easy = None
        self.status = "not loaded yet"

    @property
    def ready(self):
        return self._model is not None

    def load(self):
        with self._load_lock:
            if self._tried:
                return
            self._tried = True
            path = Config.QWEN_MODEL_PATH
            if not Config.USE_EMBEDDINGS:
                self.status = "embeddings disabled, using heuristics only"
                return
            if not os.path.isdir(path):
                self.status = "model folder not found (" + path + "), using heuristics only"
                return
            try:
                import numpy as np
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(path, device=Config.CLASSIFIER_DEVICE or "cpu", local_files_only=True)
                centroid_path = os.path.join(path, "centroids.npz")
                if os.path.isfile(centroid_path):
                    data = np.load(centroid_path)
                    hard = data["hard"]
                    easy = data["easy"]
                else:
                    vecs = model.encode(HARD_EXAMPLES + EASY_EXAMPLES, normalize_embeddings=True, batch_size=8)
                    hard = np.mean(vecs[: len(HARD_EXAMPLES)], axis=0)
                    easy = np.mean(vecs[len(HARD_EXAMPLES):], axis=0)
                    try:
                        np.savez(centroid_path, hard=hard, easy=easy)
                    except Exception:
                        pass

                self._hard = hard / np.linalg.norm(hard)
                self._easy = easy / np.linalg.norm(easy)
                self._model = model
                self.status = "Qwen3-Embedding loaded"
            except Exception as exc:
                self.status = ("embedding load failed: " + str(exc))[:200]

    def classify(self, prompt, preference=None):
        """Analyzes prompt complexity and returns 3-tier routing decision."""
        started = time.perf_counter()
        heuristic = heuristic_score(prompt)
        score, method = heuristic, "heuristic"

        if self._model is not None and self._hard is not None and self._easy is not None:
            try:
                with self._enc_lock:
                    vec = self._model.encode([prompt[:512]], normalize_embeddings=True)[0]
                margin = float(vec @ self._hard) - float(vec @ self._easy)
                emb = 1.0 / (1.0 + math.exp(-14.0 * margin))
                score, method = 0.65 * emb + 0.35 * heuristic, "embedding"
            except Exception:
                pass

        score = max(0.0, min(1.0, score))
        caps = detect_capabilities(prompt)

        # 3-Tier Classification
        simple_max = getattr(Config, "COMPLEXITY_SIMPLE_MAX", 0.35)
        complex_min = getattr(Config, "COMPLEXITY_COMPLEX_MIN", 0.70)

        if score < simple_max:
            tier = "simple"
            suggested_model = SLOTS.get("free", {}).get("model") or "gemini-3.8-flash"
            est_cost = "free"
            latency_tier = "low (<1s)"
        elif score < complex_min:
            tier = "medium"
            suggested_model = getattr(Config, "GEMINI_MEDIUM_MODEL", "gemini-3.8-flash")
            est_cost = "low"
            latency_tier = "medium (1-2s)"
        else:
            tier = "complex"
            suggested_model = getattr(Config, "GEMINI_COMPLEX_MODEL", "gemini-3.8-pro")
            est_cost = "standard"
            latency_tier = "reasoning (2-5s)"

        # User preference override
        pref = (preference or getattr(Config, "ROUTING_PREFERENCE", "auto")).lower()
        if pref in ("speed", "fast", "low_cost"):
            tier = "simple"
            suggested_model = SLOTS.get("free", {}).get("model") or "gemini-3.8-flash"
        elif pref in ("quality", "pro", "premium"):
            tier = "complex"
            suggested_model = getattr(Config, "GEMINI_COMPLEX_MODEL", "gemini-3.8-pro")

        # Legacy tier mapping for backward compatibility:
        # 'strong' for complex prompts, 'weak' for simple & medium prompts
        legacy_tier = "strong" if (tier == "complex" or score >= Config.CLASSIFIER_THRESHOLD) else "weak"

        return {
            "tier": tier,
            "legacy_tier": legacy_tier,
            "score": round(score, 3),
            "method": method,
            "capabilities": caps,
            "suggested_model": suggested_model,
            "estimated_cost": est_cost,
            "latency_tier": latency_tier,
            "classify_ms": int((time.perf_counter() - started) * 1000),
        }


classifier = Classifier()
rule_based_classifier = RuleBasedClassifier()

import threading as _threading
_threading.Thread(target=classifier.load, daemon=True).start()
