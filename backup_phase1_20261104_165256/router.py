"""Prompt classifier: Qwen3-Embedding similarity + cheap heuristics -> 'strong' or 'weak'."""
import math
import os
import re
import threading
import time

from config import Config

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
    r"\b(prove|proof|derive|derivation|optimi[sz]e|architecture|architect|system design|algorithm|"
    r"complexity|refactor|debug|stack ?trace|implement|regression|theorem|integral|differential|"
    r"step[- ]by[- ]step|analy[sz]e|analysis|trade-?offs?|kubernetes|dockerfile|sql|concurren\w+|"
    r"race condition|deadlock|security|vulnerab\w+|legal|contract|diagnos\w+|strategy|business plan|"
    r"scalab\w+|distributed|microservices?|benchmark|from scratch)\b",
    re.I,
)
_CODE = re.compile(r"```|\bdef \w+\(|\bclass \w+|\bfunction\s*\w*\(|#include|\bimport \w+|=>|\bSELECT\b.+\bFROM\b", re.I | re.S)
_GREET = re.compile(r"^\s*(hi|hello|hey|yo|thanks|thank you|ok|okay|good (morning|evening|night)|namaste|kem cho)\b", re.I)


def heuristic_score(prompt):
    words = len(prompt.split())
    score = 0.1
    if words > 150:
        score += 0.3
    elif words > 60:
        score += 0.2
    elif words > 25:
        score += 0.08
    if _CODE.search(prompt):
        score += 0.3
    score += min(0.4, 0.15 * len(_HARD.findall(prompt)))
    if prompt.count("?") >= 3:
        score += 0.1
    if _GREET.match(prompt) and words <= 8:
        score = 0.0
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

                model = SentenceTransformer(path, device=Config.CLASSIFIER_DEVICE or None)
                vecs = model.encode(HARD_EXAMPLES + EASY_EXAMPLES, normalize_embeddings=True, batch_size=8)
                hard = np.mean(vecs[: len(HARD_EXAMPLES)], axis=0)
                easy = np.mean(vecs[len(HARD_EXAMPLES):], axis=0)
                self._hard = hard / np.linalg.norm(hard)
                self._easy = easy / np.linalg.norm(easy)
                self._model = model
                self.status = "Qwen3-Embedding loaded"
            except Exception as exc:  # missing package, bad weights, out of memory...
                self.status = ("embedding load failed: " + str(exc))[:200]

    def classify(self, prompt):
        started = time.perf_counter()
        self.load()
        heuristic = heuristic_score(prompt)
        score, method = heuristic, "heuristic"
        if self._model is not None:
            try:
                with self._enc_lock:
                    vec = self._model.encode([prompt[:512]], normalize_embeddings=True)[0]
                margin = float(vec @ self._hard) - float(vec @ self._easy)
                emb = 1.0 / (1.0 + math.exp(-14.0 * margin))
                score, method = 0.65 * emb + 0.35 * heuristic, "embedding"
            except Exception:
                pass
        tier = "strong" if score >= Config.CLASSIFIER_THRESHOLD else "weak"
        return {
            "tier": tier,
            "score": round(score, 3),
            "method": method,
            "classify_ms": int((time.perf_counter() - started) * 1000),
        }


classifier = Classifier()

import threading as _threading
_threading.Thread(target=classifier.load, daemon=True).start()
