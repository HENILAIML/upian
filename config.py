import os
import certifi

# Fix broken system TLS CA bundles (e.g. invalid PostgreSQL CURL_CA_BUNDLE pointing to missing file)
for _ca_var in ("CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
    _val = os.environ.get(_ca_var)
    if _val and not os.path.exists(_val):
        os.environ.pop(_ca_var, None)

_valid_cert = certifi.where()
if not os.environ.get("REQUESTS_CA_BUNDLE") or not os.path.exists(os.environ["REQUESTS_CA_BUNDLE"]):
    os.environ["REQUESTS_CA_BUNDLE"] = _valid_cert
if not os.environ.get("SSL_CERT_FILE") or not os.path.exists(os.environ["SSL_CERT_FILE"]):
    os.environ["SSL_CERT_FILE"] = _valid_cert

from datetime import timedelta
import logging
from dotenv import load_dotenv

logger = logging.getLogger("upian.config")

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


# ---- Plans: 1. Free, 2. Pro, 3. Pro Plus ----
PLANS = {
    "free": {
        "id": "free",
        "label": "Free",
        "price": 0,
        "price_str": "$0",
        "price_inr": 0,
        "price_inr_str": "₹0",
        "price_yearly": 0,
        "price_yearly_str": "$0",
        "price_inr_yearly": 0,
        "price_inr_yearly_str": "₹0",
        "period": "forever",
        "description": "Essential multimodal gateway with daily community quota.",
        "daily": env_int("FREE_DAILY", 20),
        "monthly": env_int("FREE_MONTHLY", 300),
        "strong_monthly": env_int("FREE_STRONG_MONTHLY", 40),
        "image_gen": env_bool("FREE_IMAGE_GEN", False),
        "image_daily": env_int("FREE_IMAGE_DAILY", 0),
        "features": [
            "20 requests per day",
            "300 requests per month",
            "40 complex reasoning calls",
            "AI image generation: Not included (Upgrade to Pro)",
            "Text generation & intelligent routing",
            "MobileNetV3 image classification",
            "Basic document export (TXT, CSV, JSON)",
        ],
    },
    "plus": {
        "id": "plus",
        "label": "Pro",
        "price": 15,
        "price_str": "$15",
        "price_inr": 999,
        "price_inr_str": "₹999",
        "price_yearly": 150,
        "price_yearly_str": "$150",
        "price_inr_yearly": 9999,
        "price_inr_yearly_str": "₹9,999",
        "period": "/month",
        "description": "Priority speed, higher quota & AI image generation.",
        "daily": env_int("PLUS_DAILY", 100),
        "monthly": env_int("PLUS_MONTHLY", 2000),
        "strong_monthly": env_int("PLUS_STRONG_MONTHLY", 400),
        "image_gen": True,
        "image_daily": env_int("PLUS_IMAGE_DAILY", 20),
        "features": [
            "100 requests per day",
            "2,000 requests per month",
            "400 complex reasoning calls",
            "20 AI generated images per day (Dedicated Image API)",
            "Priority low-latency model queue",
            "Full document exports (PDF, Word, Excel, CSV, JSON)",
        ],
    },
    "plus_pro": {
        "id": "plus_pro",
        "label": "Pro Plus",
        "price": 39,
        "price_str": "$39",
        "price_inr": 2499,
        "price_inr_str": "₹2,499",
        "price_yearly": 390,
        "price_yearly_str": "$390",
        "price_inr_yearly": 24999,
        "price_inr_yearly_str": "₹24,999",
        "period": "/month",
        "description": "Maximum reasoning quota, unlimited image generation & dedicated speed.",
        "daily": env_int("PRO_DAILY", 300),
        "monthly": env_int("PRO_MONTHLY", 6000),
        "strong_monthly": env_int("PRO_STRONG_MONTHLY", 2000),
        "image_gen": True,
        "image_daily": env_int("PRO_IMAGE_DAILY", 30),
        "features": [
            "300 requests per day",
            "6,000 requests per month",
            "2,000 complex reasoning calls",
            "30 AI generated images per day (Dedicated Image API)",
            "Deep multimodal vision & OCR extraction",
            "24/7 dedicated throughput & priority SLA",
        ],
    },
}
# Aliases for flexibility
PLANS["pro"] = PLANS["plus"]
PLANS["pro_plus"] = PLANS["plus_pro"]

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
    # Direct Gemini Key (defaults to gemini-3.5-flash-lite if not specified)
    key = env("GEMINI_API_KEY")
    model = env("GEMINI_MODEL", "gemini-3.5-flash-lite") or "gemini-3.5-flash-lite"
    if key:
        slots["free"] = {
            "name": "free",
            "provider": "gemini",
            "api_key": key,
            "model": model,
            "base_url": env("GEMINI_BASE_URL"),
        }
    # Direct OpenAI Key support (also works for Groq/OpenRouter with OPENAI_BASE_URL)
    openai_key = env("OPENAI_API_KEY")
    if openai_key and "strong_1" not in slots:
        slots["strong_1"] = {
            "name": "strong_1",
            "provider": "openai",
            "api_key": openai_key,
            "model": env("OPENAI_MODEL", "gpt-4o-mini"),
            "base_url": env("OPENAI_BASE_URL"),
        }
    # Direct Anthropic Claude Key support
    claude_key = env("ANTHROPIC_API_KEY")
    if claude_key and "strong_2" not in slots:
        slots["strong_2"] = {
            "name": "strong_2",
            "provider": "anthropic",
            "api_key": claude_key,
            "model": env("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
            "base_url": env("ANTHROPIC_BASE_URL"),
        }
    return slots


SLOTS = _build_slots()


def _model_path():
    path = env("QWEN_MODEL_PATH", "./models/Qwen3-Embedding-0.6B")
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


def _get_database_uri():
    _raw_db = env("DATABASE_URL")
    if not _raw_db:
        return "sqlite:///" + os.path.join(BASE_DIR, "upian.db")
    if _raw_db.startswith("postgres://"):
        _raw_db = _raw_db.replace("postgres://", "postgresql://", 1)

    if env_bool("ALLOW_SQLITE_FALLBACK", True):
        try:
            import psycopg2
            from urllib.parse import urlparse
            p = urlparse(_raw_db)
            conn = psycopg2.connect(
                dbname=p.path.lstrip("/"),
                user=p.username,
                password=p.password,
                host=p.hostname or "localhost",
                port=p.port or 5432,
                connect_timeout=1
            )
            conn.close()
            return _raw_db
        except Exception as exc:
            logger.warning("PostgreSQL primary database is unreachable (%s). Falling back to SQLite.", exc)
            return "sqlite:///" + os.path.join(BASE_DIR, "upian.db")
    return _raw_db


class Config:
    BASE_DIR = BASE_DIR
    SECRET_KEY = env("SECRET_KEY", "dev-secret-change-me")
    DATABASE_URL = env("DATABASE_URL") or _get_database_uri()
    SQLALCHEMY_DATABASE_URI = _get_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_timeout": 5,
    }
    ALLOW_SQLITE_FALLBACK = env_bool("ALLOW_SQLITE_FALLBACK", True)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_bool("COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)

    # Payment Gateway (Razorpay & Multi-currency INR/USD)
    RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET", "")
    PAYMENT_CURRENCY = env("PAYMENT_CURRENCY", "USD") # "USD" or "INR"

    # Google OAuth
    GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_PROJECT_ID = env("GOOGLE_PROJECT_ID", "upian-515405")
    GOOGLE_REDIRECT_URI = env("GOOGLE_REDIRECT_URI", "")

    HOST = env("HOST", "127.0.0.1")
    PORT = env_int("PORT", 5000)

    ADMIN_NAME = env("ADMIN_NAME", "Admin")
    ADMIN_EMAIL = env("ADMIN_EMAIL")
    ADMIN_PASSWORD = env("ADMIN_PASSWORD")

    APP_TIMEZONE = env("APP_TIMEZONE", "Asia/Kolkata")

    # Classifier & Complexity Routing
    QWEN_MODEL_PATH = _model_path()
    USE_EMBEDDINGS = env_bool("USE_EMBEDDINGS", True)
    PRELOAD_CLASSIFIER = env_bool("PRELOAD_CLASSIFIER", True)
    CLASSIFIER_DEVICE = env("CLASSIFIER_DEVICE")  # "", "cpu" or "cuda"
    CLASSIFIER_THRESHOLD = env_float("CLASSIFIER_THRESHOLD", 0.5)
    ROUTING_PREFERENCE = env("ROUTING_PREFERENCE", "auto")  # "auto", "speed", "quality"
    COMPLEXITY_SIMPLE_MAX = env_float("COMPLEXITY_SIMPLE_MAX", 0.35)
    COMPLEXITY_COMPLEX_MIN = env_float("COMPLEXITY_COMPLEX_MIN", 0.70)

    # LLM calls & Tier Models
    GEMINI_MEDIUM_MODEL = env("GEMINI_MEDIUM_MODEL", "gemini-3.5-flash-lite")
    GEMINI_COMPLEX_MODEL = env("GEMINI_COMPLEX_MODEL", "gemini-2.5-pro")
    LLM_CONNECT_TIMEOUT = env_int("LLM_CONNECT_TIMEOUT", 3)
    LLM_TIMEOUT = env_int("LLM_TIMEOUT", 35)
    MAX_OUTPUT_TOKENS = env_int("MAX_OUTPUT_TOKENS", 4096)
    MAX_PROMPT_CHARS = env_int("MAX_PROMPT_CHARS", 6000)
    HISTORY_TURNS = env_int("HISTORY_TURNS", 8)

    # Multimodal Vision
    VISION_MOBILENET_MODEL = env("VISION_MOBILENET_MODEL", "mobilenet_v3_small")
    VISION_SIGLIP_MODEL = env("VISION_SIGLIP_MODEL", "google/siglip-base-patch16-224")
    VISION_QWEN_3B_BASE_URL = env("VISION_QWEN_3B_BASE_URL", "http://localhost:11434/v1")
    VISION_QWEN_3B_MODEL = env("VISION_QWEN_3B_MODEL", "qwen2.5-vl:3b")
    VISION_QWEN_3B_API_KEY = env("VISION_QWEN_3B_API_KEY", "")
    VISION_DEVICE = env("VISION_DEVICE", "cpu")

    # Image Generation
    IMAGE_GEN_API_KEY = env("IMAGE_GEN_API_KEY", "")
    OPENAI_API_KEY = env("OPENAI_API_KEY", "")
    STABILITY_API_KEY = env("STABILITY_API_KEY", "")
    IMAGE_GEN_PROVIDER = env("IMAGE_GEN_PROVIDER", "auto")
    IMAGE_GEN_LOW_MODEL = env("IMAGE_GEN_LOW_MODEL", "dall-e-2")
    IMAGE_GEN_PREMIUM_MODEL = env("IMAGE_GEN_PREMIUM_MODEL", "dall-e-3")

    # File upload / read / create (no vector database)
    UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
    FILE_MAX_MB = env_int("FILE_MAX_MB", 20)
    FILE_USER_QUOTA_MB = env_int("FILE_USER_QUOTA_MB", 200)
    FILE_MAX_COUNT = env_int("FILE_MAX_COUNT", 50)
    FILE_MAX_CONTEXT_CHARS = env_int("FILE_MAX_CONTEXT_CHARS", 24000)  # text from files sent to the model per question
    MAX_CONTENT_LENGTH = (env_int("FILE_MAX_MB", 20) + 2) * 1024 * 1024
    SUPPORTED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
    SUPPORTED_DOC_EXT = {".pdf", ".docx", ".xlsx", ".txt", ".csv"}
    EXTRACT_FORMATS = ("pdf", "docx", "xlsx", "csv", "txt", "json")


def validate_config():
    """
    Validates application configuration at startup.
    - If a required configuration item is missing or invalid, logs a clear ERROR naming the missing item.
    - If optional configuration items (like external API keys) are empty, logs a WARN.
    Returns (is_valid: bool, errors: list[str], warnings: list[str]).
    """
    errors = []
    warnings = []

    # 1. Required: SECRET_KEY
    secret_key = Config.SECRET_KEY
    if not secret_key:
        errors.append("Missing required environment variable: SECRET_KEY")
    elif secret_key in ("dev-secret-change-me", "change-this-to-a-secure-random-secret-key-in-production"):
        warnings.append("SECRET_KEY is using a default development value. Set a strong random secret in production.")

    # 2. Required: HOST
    host = Config.HOST
    if not host:
        errors.append("Missing required environment variable: HOST")

    # 3. Required: PORT
    port = Config.PORT
    if not isinstance(port, int) or port < 1 or port > 65535:
        errors.append(f"Invalid PORT value '{port}': Must be an integer between 1 and 65535.")

    # 4. Required: DATABASE_URL / SQLALCHEMY_DATABASE_URI
    db_uri = Config.SQLALCHEMY_DATABASE_URI
    if not db_uri:
        errors.append("Missing required configuration: DATABASE_URL (or SQLALCHEMY_DATABASE_URI)")
    elif not (db_uri.startswith("postgresql://") or db_uri.startswith("sqlite:///")):
        warnings.append(f"DATABASE_URL protocol '{db_uri.split('://')[0]}' may not be supported. Recommended: postgresql:// or sqlite:///")

    # 5. Optional Text Generation Keys
    text_keys = [
        ("GEMINI_API_KEY", env("GEMINI_API_KEY")),
        ("OPENAI_API_KEY", env("OPENAI_API_KEY")),
        ("ANTHROPIC_API_KEY", env("ANTHROPIC_API_KEY")),
    ]
    has_text_key = any(bool(val) for _, val in text_keys)
    if not has_text_key:
        warnings.append("No text-generation API key configured (GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY). App will run in mock/simulation mode.")

    # 6. Optional Image Generation Keys (Strictly separate from text keys)
    image_keys = [
        ("IMAGE_GEN_API_KEY", env("IMAGE_GEN_API_KEY")),
        ("STABILITY_API_KEY", env("STABILITY_API_KEY")),
        ("IMAGEN_API_KEY", env("IMAGEN_API_KEY")),
    ]
    has_image_key = any(bool(val) for _, val in image_keys)
    image_provider = env("IMAGE_GEN_PROVIDER", "auto").lower()
    if not has_image_key and image_provider != "mock":
        warnings.append("No dedicated image-generation API key configured (IMAGE_GEN_API_KEY, STABILITY_API_KEY, IMAGEN_API_KEY). Image generation will return an unconfigured notice or mock asset.")

    # 7. Optional Google OAuth
    if not Config.GOOGLE_CLIENT_ID:
        warnings.append("No GOOGLE_CLIENT_ID configured. Google OAuth sign-in will be unavailable.")

    for err in errors:
        logger.error("[CONFIG ERROR] %s", err)
    for warn in warnings:
        logger.warning("[CONFIG WARN] %s", warn)

    is_valid = len(errors) == 0
    return is_valid, errors, warnings
