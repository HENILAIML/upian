import os
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def env(name, default=""):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().strip('"').strip("'")


def env_int(name, default):
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def env_float(name, default):
    try:
        return float(env(name, str(default)))
    except ValueError:
        return default


def env_bool(name, default):
    value = env(name, "").lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


# ---- Plans: daily limit, monthly limit, and monthly cap on strong-model calls ----
PLANS = {
    "free": {
        "label": "Free",
        "daily": env_int("FREE_DAILY", 20),
        "monthly": env_int("FREE_MONTHLY", 300),
        "strong_monthly": env_int("FREE_STRONG_MONTHLY", 40),
    },
    "plus": {
        "label": "Plus",
        "daily": env_int("PLUS_DAILY", 100),
        "monthly": env_int("PLUS_MONTHLY", 2000),
        "strong_monthly": env_int("PLUS_STRONG_MONTHLY", 400),
    },
    "plus_pro": {
        "label": "Plus Pro",
        "daily": env_int("PRO_DAILY", 300),
        "monthly": env_int("PRO_MONTHLY", 6000),
        "strong_monthly": env_int("PRO_STRONG_MONTHLY", 2000),
    },
}
PLAN_ORDER = ["free", "plus", "plus_pro"]

# ---- Provider slots (5 in total). A slot is active only if it has a key AND a model. ----
SLOT_ORDER = ["strong_1", "strong_2", "weak_1", "weak_2", "free"]


def _build_slots():
    slots = {}
    for name in SLOT_ORDER[:4]:
        prefix = name.upper()
        key, model = env(prefix + "_API_KEY"), env(prefix + "_MODEL")
        if key and model:
            slots[name] = {
                "name": name,
                "provider": env(prefix + "_PROVIDER", "gemini").lower(),
                "api_key": key,
                "model": model,
                "base_url": env(prefix + "_BASE_URL"),
            }
    key, model = env("GEMINI_API_KEY"), env("GEMINI_MODEL")
    if key and model:
        slots["free"] = {
            "name": "free",
            "provider": "gemini",
            "api_key": key,
            "model": model,
            "base_url": env("GEMINI_BASE_URL"),
        }
    return slots


SLOTS = _build_slots()


def _model_path():
    path = env("QWEN_MODEL_PATH", "./models/Qwen3-Embedding-0.6B")
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


class Config:
    SECRET_KEY = env("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = env("DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "upian.db"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_bool("COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)

    HOST = env("HOST", "127.0.0.1")
    PORT = env_int("PORT", 5000)

    ADMIN_NAME = env("ADMIN_NAME", "Admin")
    ADMIN_EMAIL = env("ADMIN_EMAIL")
    ADMIN_PASSWORD = env("ADMIN_PASSWORD")

    APP_TIMEZONE = env("APP_TIMEZONE", "Asia/Kolkata")

    # Classifier
    QWEN_MODEL_PATH = _model_path()
    USE_EMBEDDINGS = env_bool("USE_EMBEDDINGS", True)
    PRELOAD_CLASSIFIER = env_bool("PRELOAD_CLASSIFIER", True)
    CLASSIFIER_DEVICE = env("CLASSIFIER_DEVICE")  # "", "cpu" or "cuda"
    CLASSIFIER_THRESHOLD = env_float("CLASSIFIER_THRESHOLD", 0.5)

    # LLM calls
    LLM_TIMEOUT = env_int("LLM_TIMEOUT", 60)
    MAX_OUTPUT_TOKENS = env_int("MAX_OUTPUT_TOKENS", 4096)
    MAX_PROMPT_CHARS = env_int("MAX_PROMPT_CHARS", 6000)
    HISTORY_TURNS = env_int("HISTORY_TURNS", 8)

    # File upload / read / create (no vector database)
    UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
    FILE_MAX_MB = env_int("FILE_MAX_MB", 20)
    FILE_USER_QUOTA_MB = env_int("FILE_USER_QUOTA_MB", 200)
    FILE_MAX_COUNT = env_int("FILE_MAX_COUNT", 50)
    FILE_MAX_CONTEXT_CHARS = env_int("FILE_MAX_CONTEXT_CHARS", 24000)  # text from files sent to the model per question
    MAX_CONTENT_LENGTH = (env_int("FILE_MAX_MB", 20) + 2) * 1024 * 1024
