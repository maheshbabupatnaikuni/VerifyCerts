import os
import sys
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parents[2]
CORE_DIR = BASE_DIR / "core"
BACKEND_APPS_DIR = BASE_DIR / "backend" / "apps"

for path in (CORE_DIR, BACKEND_APPS_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
from dotenv import load_dotenv

# Ensure project-local .env values are used even if machine-level env vars exist.
load_dotenv(BASE_DIR / ".env", override=True)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "test-only-local-secret-key-change-before-hosting")
DEBUG = os.getenv("DJANGO_DEBUG", "True") == "True"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")


def _get_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "accounts",
    "certificates",
    "blockchain_app",
    "verification",
    "branding",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "university_verify.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "branding.context_processors.branding_config",
            ],
        },
    },
]

WSGI_APPLICATION = "university_verify.wsgi.application"
ASGI_APPLICATION = "university_verify.asgi.application"

DB_ENGINE = os.getenv("DB_ENGINE", "sqlite")
if DB_ENGINE == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "verifycerts"),
            "USER": os.getenv("POSTGRES_USER", "verifycerts"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
            "HOST": os.getenv("POSTGRES_HOST", "db"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "database" / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "runtime" / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "frontend" / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "runtime" / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
}

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_HTTPONLY = True

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "15"))
CERTIFICATE_ID_FORMAT = os.getenv("CERTIFICATE_ID_FORMAT", "{prefix}-{year}-{dept}-{seq:06d}")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
VERIFY_BASE_URL = os.getenv("VERIFY_BASE_URL", f"{PUBLIC_BASE_URL}/verify")

WEB3_PROVIDER_URI = os.getenv("WEB3_PROVIDER_URI", "")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "")
CONTRACT_ABI_PATH = os.getenv(
    "CONTRACT_ABI_PATH",
    str(BASE_DIR / "blockchain" / "contracts" / "CertificateRegistry.abi.json"),
)
ISSUER_PRIVATE_KEY = os.getenv("ISSUER_PRIVATE_KEY", "")
ISSUER_WALLET_ADDRESS = os.getenv("ISSUER_WALLET_ADDRESS", "")
CHAIN_ID = int(os.getenv("CHAIN_ID", "80001"))
CHAIN_DISPLAY_NAME = os.getenv("CHAIN_DISPLAY_NAME", "").strip()
if not CHAIN_DISPLAY_NAME:
    if CHAIN_ID == 1337:
        CHAIN_DISPLAY_NAME = "Ganache Local"
    elif CHAIN_ID == 80002:
        CHAIN_DISPLAY_NAME = "Polygon Amoy"
    elif CHAIN_ID == 80001:
        CHAIN_DISPLAY_NAME = "Polygon Network"
    elif CHAIN_ID == 1:
        CHAIN_DISPLAY_NAME = "Ethereum Mainnet"
    else:
        CHAIN_DISPLAY_NAME = f"Chain {CHAIN_ID}"
CHAIN_EXPLORER_TX_BASE = os.getenv("CHAIN_EXPLORER_TX_BASE", "")
CHAIN_EXPLORER_ADDRESS_BASE = os.getenv("CHAIN_EXPLORER_ADDRESS_BASE", "")

IPFS_GATEWAY = os.getenv("IPFS_GATEWAY", "https://ipfs.io/ipfs")
PINATA_JWT = os.getenv("PINATA_JWT", "")
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "tesseract")

CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",") if o.strip()]

CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _get_bool_env("SECURE_HSTS_INCLUDE_SUBDOMAINS", not DEBUG)
SECURE_HSTS_PRELOAD = _get_bool_env("SECURE_HSTS_PRELOAD", not DEBUG)
# Local development uses HTTP. Hosting can force HTTPS with SECURE_SSL_REDIRECT=True.
SECURE_SSL_REDIRECT = _get_bool_env("SECURE_SSL_REDIRECT", False)
SESSION_COOKIE_SECURE = _get_bool_env("SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = _get_bool_env("CSRF_COOKIE_SECURE", not DEBUG)

# Test runner must not force HTTPS redirects; otherwise API tests receive 301 responses.
if "test" in sys.argv:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.example.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "no-reply@example.edu")
CERTIFICATE_EMAIL_FALLBACK = os.getenv("CERTIFICATE_EMAIL_FALLBACK", "verification@example.edu")
INSTITUTION_EMAIL_DOMAIN = os.getenv("INSTITUTION_EMAIL_DOMAIN", "example.edu").strip()

SUPER_ADMIN_USERNAME = os.getenv("SUPER_ADMIN_USERNAME", "admin")
SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD", "test-only-local-admin-password")
SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "admin@example.edu")

LOGIN_REDIRECT_URL = "/"
LOGIN_URL = "/accounts/login/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
