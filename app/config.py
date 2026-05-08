import logging
import os
from datetime import timedelta

from dotenv import load_dotenv


load_dotenv()

logger = logging.getLogger(__name__)

_REQUIRED_ENV_VARS = [
    "SECRET_KEY",
    "FLASK_ENV",
    "PORT",
    "DATABASE_URL",
    "REDIS_URL",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "MAIL_SERVER",
    "MAIL_PORT",
    "MAIL_USERNAME",
    "MAIL_PASSWORD",
    "MAIL_DEFAULT_SENDER",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "TEMP_DOWNLOAD_DIR",
    "MAX_FILE_AGE_SECONDS",
    "SENTRY_DSN",
    "PROXY_PROVIDER_URL",
    "PROXY_PROVIDER_KEY",
    "PAGERDUTY_INTEGRATION_KEY",
]


def _warn_missing_env_vars():
    for key in _REQUIRED_ENV_VARS:
        if not os.environ.get(key):
            logger.warning("Missing environment variable: %s", key)


_warn_missing_env_vars()


def _sqlite_database_uri(filename: str) -> str:
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    database_path = os.path.join(base_dir, filename).replace("\\", "/")
    return f"sqlite:///{database_path}"


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "fallback-dev-key-change-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql://universaldl:password@localhost:5432/universaldl",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_recycle": 300, "pool_pre_ping": True}

    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL = REDIS_URL
    CELERY_RESULT_BACKEND = REDIS_URL

    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@universaldl.com")

    RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
    RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")
    RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET")

    TEMP_DOWNLOAD_DIR = os.environ.get("TEMP_DOWNLOAD_DIR", "/tmp/universaldl")
    MAX_FILE_AGE_SECONDS = int(os.environ.get("MAX_FILE_AGE_SECONDS", 3600))

    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

    SENTRY_DSN = os.environ.get("SENTRY_DSN")

    PROXY_PROVIDER_URL = os.environ.get("PROXY_PROVIDER_URL")
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600

    RATELIMIT_STORAGE_URL = REDIS_URL

    APP_VERSION = "1.0.0"

    PRO_PRICE_MONTHLY_PAISE = 79900
    PRO_PRICE_ANNUAL_PAISE = 639900
class DevelopmentConfig(BaseConfig):
    DEBUG = True
    TESTING = False
    SQLALCHEMY_ECHO = False
    SQLALCHEMY_DATABASE_URI = _sqlite_database_uri("universaldl_dev.db")


class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_DURATION = timedelta(days=30)


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": BaseConfig,
}
