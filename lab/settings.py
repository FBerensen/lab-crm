import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False

ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "lab",
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

ROOT_URLCONF = "lab.urls"
WSGI_APPLICATION = "lab.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
    },
}]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "labcrm"),
        "USER": "labcrm",
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": "db",
        "PORT": "5432",
        "CONN_MAX_AGE": 60,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
TIME_ZONE = os.getenv("TIME_ZONE", "Europe/Moscow")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Только для первой установки. Перед обновлениями нужны штатные миграции.
MIGRATION_MODULES = {"lab": None}

SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "1") == "1"
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "labcrm@localhost")
REPORT_RECIPIENTS = [
    x.strip()
    for x in os.getenv("REPORT_RECIPIENTS", "").split(",")
    if x.strip()
]

# LABCRM_STOCK_MIGRATIONS_V1
MIGRATION_MODULES = {**globals().get('MIGRATION_MODULES', {}), 'lab': 'lab.migrations'}

# LABCRM_STOCK_MIGRATIONS_READY
MIGRATION_MODULES = {**globals().get('MIGRATION_MODULES', {}), 'lab': 'lab.migrations'}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "container_console": {
            "class": "logging.StreamHandler",
            "level": "ERROR",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["container_console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

# LABCRM_STAGE8B_TEMPLATE_PATH
from pathlib import Path as _LabCRMTemplatePath8B
_labcrm_nav8b_dir = _LabCRMTemplatePath8B(__file__).resolve().parents[1] / 'templates8b'
_labcrm_nav8b_engine = TEMPLATES[0]
if not _labcrm_nav8b_engine['BACKEND'].endswith('django.DjangoTemplates'):
    raise RuntimeError('8B template backend changed; review configuration')
_labcrm_nav8b_engine['DIRS'] = [_labcrm_nav8b_dir] + [
    _path for _path in (_labcrm_nav8b_engine.get('DIRS') or [])
    if _LabCRMTemplatePath8B(_path).resolve() != _labcrm_nav8b_dir
]
