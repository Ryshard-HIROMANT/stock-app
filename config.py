import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    # SECRET_KEY обязателен и передаётся через переменную окружения.
    SECRET_KEY = os.environ.get("SECRET_KEY")
    if not SECRET_KEY:
        raise RuntimeError("SECRET_KEY environment variable is required")

    db_path = os.environ.get("DB_PATH", str(BASE_DIR / "stock.db"))
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or f"sqlite:///{db_path}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    secure_cookie_default = "true" if os.environ.get("RENDER", "").lower() == "true" else "false"
    SESSION_COOKIE_SECURE = os.environ.get(
        "SESSION_COOKIE_SECURE", secure_cookie_default
    ).lower() in {"1", "true", "yes"}

    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
