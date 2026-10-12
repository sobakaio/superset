import logging
import os
from datetime import timedelta
from urllib.parse import quote

from celery.schedules import crontab
from flask_caching.backends.rediscache import RedisCache
from redis import Redis
from sqlalchemy.engine import URL
from superset.config import (
    TALISMAN_CONFIG as DEFAULT_TALISMAN_CONFIG,
    WTF_CSRF_EXEMPT_LIST as DEFAULT_WTF_CSRF_EXEMPT_LIST,
)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def csv_env(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


SECRET_KEY = required_env("SUPERSET_SECRET_KEY")

SQLALCHEMY_DATABASE_URI = URL.create(
    drivername="postgresql+psycopg2",
    username=required_env("SUPERSET_METADATA_DB_USER"),
    password=required_env("SUPERSET_METADATA_DB_PASSWORD"),
    host=required_env("SUPERSET_METADATA_DB_HOST"),
    port=int(os.getenv("SUPERSET_METADATA_DB_PORT", "5432")),
    database=required_env("SUPERSET_METADATA_DB_NAME"),
).render_as_string(hide_password=False)

SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "pool_size": 5,
    "max_overflow": 10,
}

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = required_env("REDIS_PASSWORD")


def redis_url(db: int) -> str:
    password = quote(REDIS_PASSWORD, safe="")
    return f"redis://:{password}@{REDIS_HOST}:{REDIS_PORT}/{db}"


def redis_cache(prefix: str, db: int, timeout: int) -> dict:
    return {
        "CACHE_TYPE": "RedisCache",
        "CACHE_DEFAULT_TIMEOUT": timeout,
        "CACHE_KEY_PREFIX": prefix,
        "CACHE_REDIS_URL": redis_url(db),
    }


# Keep each workload isolated by Redis database and/or key prefix.
CACHE_CONFIG = redis_cache("superset_metadata_", 2, 300)
DATA_CACHE_CONFIG = redis_cache("superset_data_", 2, 300)
FILTER_STATE_CACHE_CONFIG = redis_cache("superset_filter_", 2, 86400)
EXPLORE_FORM_DATA_CACHE_CONFIG = redis_cache("superset_explore_", 2, 86400)

RESULTS_BACKEND = RedisCache(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    db=1,
    key_prefix="superset_results_",
)

DISTRIBUTED_COORDINATION_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_REDIS_HOST": REDIS_HOST,
    "CACHE_REDIS_PORT": REDIS_PORT,
    "CACHE_REDIS_DB": 4,
    "CACHE_REDIS_PASSWORD": REDIS_PASSWORD,
}


class CeleryConfig:
    broker_url = redis_url(0)
    result_backend = redis_url(1)
    task_default_queue = "default"
    imports = (
        "superset.sql_lab",
        "superset.tasks.scheduler",
        "superset.tasks.cache",
    )
    worker_prefetch_multiplier = 10
    task_acks_late = True
    broker_connection_retry_on_startup = True
    task_annotations = {
        "sql_lab.get_sql_results": {
            "rate_limit": "100/s",
            "queue": "sql_lab",
        },
        "reports.scheduler": {
            "queue": "email_reports",
        },
        "reports.execute": {
            "queue": "email_reports",
        },
        "reports.prune_log": {
            "queue": "email_reports",
        },
    }
    beat_schedule = {
        "reports.scheduler": {
            "task": "reports.scheduler",
            "schedule": crontab(minute="*", hour="*"),
        },
        "reports.prune_log": {
            "task": "reports.prune_log",
            "schedule": crontab(minute=0, hour=0),
        },
        "prune_query_history": {
            "task": "prune_query",
            "schedule": crontab(minute=0, hour=0, day_of_month=1),
            "kwargs": {"retention_period_days": 180},
        },
    }


CELERY_CONFIG = CeleryConfig

FEATURE_FLAGS = {
    "ALERT_REPORTS": True,
    "ALERT_REPORT_SLACK_V2": True,
    "ALERT_REPORT_WEBHOOK": True,
    "DATE_FORMAT_IN_EMAIL_SUBJECT": True,
    "DISABLE_EMBEDDED_SUPERSET_LOGOUT": True,
    "EMBEDDED_SUPERSET": True,
    "PLAYWRIGHT_REPORTS_AND_THUMBNAILS": True,
}

# Dashboard embedding uses short-lived guest tokens issued by the host
# application's backend. Keep external frame origins deployment-specific in
# .env; the checked-in defaults are for local development only.
EMBED_ALLOWED_ORIGINS = csv_env(
    "SUPERSET_EMBED_ALLOWED_ORIGINS",
    [
        "http://localhost",
        "http://localhost:8080",
        "http://localhost:8081",
        "http://localhost:8088",
    ],
)
GUEST_ROLE_NAME = os.getenv("GUEST_ROLE_NAME", "Embed").strip() or "Embed"
GUEST_TOKEN_JWT_SECRET = required_env("GUEST_TOKEN_JWT_SECRET")
GUEST_TOKEN_JWT_EXP_SECONDS = int(os.getenv("GUEST_TOKEN_JWT_EXP_SECONDS", "300"))

# MCP is a separate FastMCP process, but it loads this same Superset config.
# Keep JWT authentication and RBAC enabled by default; disabling either is only
# appropriate for a short-lived, isolated development test.
SUPERSET_WEBSERVER_ADDRESS = "http://superset-web:8088"
MCP_SERVICE_HOST = os.getenv("MCP_SERVICE_HOST", "0.0.0.0")
MCP_SERVICE_PORT = int(os.getenv("MCP_SERVICE_PORT", "5008"))
MCP_SERVICE_URL = (
    os.getenv("MCP_SERVICE_URL", "").strip()
    or os.getenv("SUPERSET_PUBLIC_URL", "http://localhost:8088").rstrip("/")
)
MCP_AUTH_ENABLED = env_bool("MCP_AUTH_ENABLED", True)
MCP_JWT_ALGORITHM = os.getenv("MCP_JWT_ALGORITHM", "HS256").upper()
MCP_JWT_SECRET = os.getenv("MCP_JWT_SECRET", "")
MCP_JWKS_URI = os.getenv("MCP_JWKS_URI", "")
MCP_JWT_PUBLIC_KEY = os.getenv("MCP_JWT_PUBLIC_KEY", "")
MCP_JWT_ISSUER = os.getenv("MCP_JWT_ISSUER", "")
MCP_JWT_AUDIENCE = os.getenv("MCP_JWT_AUDIENCE", "")
MCP_REQUIRED_SCOPES = csv_env("MCP_REQUIRED_SCOPES", ["superset:mcp"])
MCP_RBAC_ENABLED = env_bool("MCP_RBAC_ENABLED", True)
# Superset 6.1.0 does not yet resolve the validated JWT subject into g.user.
# Keep this fixed-user fallback configurable through the uncommitted .env.
MCP_DEV_USERNAME = os.getenv("MCP_DEV_USERNAME", "").strip() or None
MCP_JWT_DEBUG_ERRORS = False
MCP_SESSION_CONFIG = {
    "SESSION_COOKIE_HTTPONLY": True,
    "SESSION_COOKIE_SECURE": env_bool("SESSION_COOKIE_SECURE", True),
    "SESSION_COOKIE_SAMESITE": "Lax",
    "SESSION_COOKIE_NAME": "superset_mcp_session",
    "PERMANENT_SESSION_LIFETIME": int(
        os.getenv("SESSION_LIFETIME_HOURS", "8")
    )
    * 3600,
}
MCP_CSRF_CONFIG = {
    "WTF_CSRF_ENABLED": True,
    "WTF_CSRF_TIME_LIMIT": None,
}
MCP_STORE_CONFIG = {
    "enabled": True,
    "CACHE_REDIS_URL": redis_url(int(os.getenv("MCP_STORE_REDIS_DB", "5"))),
    "event_store_max_events": 100,
    "event_store_ttl": 3600,
}

if MCP_AUTH_ENABLED:
    if MCP_JWT_ALGORITHM not in {"HS256", "RS256"}:
        raise RuntimeError("MCP_JWT_ALGORITHM must be HS256 or RS256")
    if MCP_JWT_ALGORITHM == "HS256" and not MCP_JWT_SECRET:
        raise RuntimeError("MCP_JWT_SECRET is required when MCP uses HS256")
    if MCP_JWT_ALGORITHM == "RS256" and not (MCP_JWKS_URI or MCP_JWT_PUBLIC_KEY):
        raise RuntimeError(
            "MCP_JWKS_URI or MCP_JWT_PUBLIC_KEY is required when MCP uses RS256"
        )

ALERT_REPORTS_NOTIFICATION_DRY_RUN = env_bool(
    "ALERT_REPORTS_NOTIFICATION_DRY_RUN", False
)
ALERT_REPORTS_WEBHOOK_HTTPS_ONLY = True

SCREENSHOT_LOCATE_WAIT = int(os.getenv("SCREENSHOT_LOCATE_WAIT", "100"))
SCREENSHOT_LOAD_WAIT = int(os.getenv("SCREENSHOT_LOAD_WAIT", "600"))

# The reports worker reaches Superset directly over the Compose network.
WEBDRIVER_BASEURL = "http://superset-web:8088/"
WEBDRIVER_BASEURL_USER_FRIENDLY = (
    os.getenv("SUPERSET_PUBLIC_URL", "http://localhost:8088").rstrip("/") + "/"
)

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_STARTTLS = env_bool("SMTP_STARTTLS", True)
SMTP_SSL = env_bool("SMTP_SSL", False)
SMTP_SSL_SERVER_AUTH = env_bool("SMTP_SSL_SERVER_AUTH", True)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_MAIL_FROM = os.getenv("SMTP_MAIL_FROM", "superset@example.com")
EMAIL_REPORTS_SUBJECT_PREFIX = "[Superset] "

SLACK_API_TOKEN = os.getenv("SLACK_API_TOKEN", "")

# Store sessions server-side in Redis; the browser receives only a signed ID.
SESSION_SERVER_SIDE = True
SESSION_TYPE = "redis"
SESSION_REDIS = Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    db=3,
)
SESSION_USE_SIGNER = True
SESSION_KEY_PREFIX = "superset_session:"
SESSION_PERMANENT = True
PERMANENT_SESSION_LIFETIME = timedelta(
    hours=int(os.getenv("SESSION_LIFETIME_HOURS", "8"))
)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

WTF_CSRF_ENABLED = True
# Guest tokens are requested only by the host application's backend with a
# short-lived Superset JWT and the dedicated issuer role. Exempting this one
# server-to-server endpoint avoids a browser-session CSRF dependency; keep all
# Superset defaults and all other CSRF protection intact.
WTF_CSRF_EXEMPT_LIST = [
    *DEFAULT_WTF_CSRF_EXEMPT_LIST,
    "superset.security.api.guest_token",
]
ENABLE_PROXY_FIX = env_bool("ENABLE_PROXY_FIX", True)
PREFERRED_URL_SCHEME = "https"

# Keep Superset's CSP/security headers enabled. TLS itself terminates upstream.
TALISMAN_ENABLED = True
TALISMAN_CONFIG = {
    **DEFAULT_TALISMAN_CONFIG,
    "content_security_policy": {
        **DEFAULT_TALISMAN_CONFIG["content_security_policy"],
        "frame-ancestors": ["'self'", *EMBED_ALLOWED_ORIGINS],
    },
    "force_https": False,
    "session_cookie_secure": SESSION_COOKIE_SECURE,
}

LOG_LEVEL = getattr(
    logging,
    os.getenv("SUPERSET_LOG_LEVEL", "INFO").upper(),
    logging.INFO,
)

# PostgreSQL connections to RDS are configured in the Superset UI.
# The required psycopg2 driver is installed in the custom image.
