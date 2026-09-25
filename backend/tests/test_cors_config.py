"""CORS: explicit allowlist; credentials never with wildcard."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import cors_config


def test_cors_config():
    from app.config import settings
    old = settings.CORS_ALLOW_ORIGINS
    try:
        settings.CORS_ALLOW_ORIGINS = ""
        cfg = cors_config()
        assert cfg["allow_origins"] != ["*"] or not cfg["allow_credentials"], cfg
        assert cfg["allow_credentials"] is False, cfg
        settings.CORS_ALLOW_ORIGINS = "https://app.example, http://localhost:5173"
        cfg = cors_config()
        assert cfg["allow_origins"] == ["https://app.example", "http://localhost:5173"], cfg
        assert cfg["allow_credentials"] is True, cfg
    finally:
        settings.CORS_ALLOW_ORIGINS = old
    print("PASS cors")


if __name__ == "__main__":
    test_cors_config()
