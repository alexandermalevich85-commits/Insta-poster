from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _get(key: str, default: str = "") -> str:
    """Get config value from environment variables."""
    return os.getenv(key, default)


# Provider selection
TEXT_PROVIDER = _get("TEXT_PROVIDER", "gemini").lower()    # claude | gemini | openai

# Model selection (empty = use provider default)
TEXT_MODEL = _get("TEXT_MODEL", "")

# API keys
CLAUDE_API_KEY = _get("CLAUDE_API_KEY")
GEMINI_API_KEY = _get("GEMINI_API_KEY")
OPENAI_API_KEY = _get("OPENAI_API_KEY")

# Vertex AI (alternative to GEMINI_API_KEY)
GOOGLE_PROJECT_ID = _get("GOOGLE_PROJECT_ID")
GOOGLE_LOCATION = _get("GOOGLE_LOCATION", "us-central1")
GOOGLE_SERVICE_ACCOUNT_JSON = _get("GOOGLE_SERVICE_ACCOUNT_JSON")

# Instagram Graph API
IG_ACCESS_TOKEN = _get("IG_ACCESS_TOKEN")
IG_USER_ID = _get("IG_USER_ID")
IG_PUBLISH_METHOD = _get("IG_PUBLISH_METHOD", "instagrapi").lower()  # graph_api | graph_api_scheduled | instagrapi

# Instagram instagrapi (fallback)
INSTAGRAPI_USERNAME = _get("INSTAGRAPI_USERNAME")
INSTAGRAPI_PASSWORD = _get("INSTAGRAPI_PASSWORD")

# Image hosting (for Graph API — images must be public URLs)
IMGBB_API_KEY = _get("IMGBB_API_KEY")

# Schedule
POSTS_PER_DAY = int(_get("POSTS_PER_DAY", "20"))
PUBLISH_START_HOUR = int(_get("PUBLISH_START_HOUR", "8"))
PUBLISH_END_HOUR = int(_get("PUBLISH_END_HOUR", "22"))
TIMEZONE = _get("TIMEZONE", "Europe/Moscow")

# GitHub (for syncing from Streamlit UI)
GITHUB_TOKEN = _get("GITHUB_TOKEN")


# ── Gemini client factory ────────────────────────────────────────────────────

_vertex_credentials_ready = False


def _setup_vertex_credentials():
    """Write service account JSON to a temp file and set GOOGLE_APPLICATION_CREDENTIALS."""
    global _vertex_credentials_ready
    if _vertex_credentials_ready:
        return

    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        _vertex_credentials_ready = True
        return

    if GOOGLE_SERVICE_ACCOUNT_JSON:
        import atexit
        import json
        import tempfile

        sa_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        fd, path = tempfile.mkstemp(suffix=".json", prefix="gcp_sa_")
        os.close(fd)
        with open(path, "w") as f:
            json.dump(sa_info, f)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path
        atexit.register(lambda p=path: os.unlink(p) if os.path.exists(p) else None)

    _vertex_credentials_ready = True


def get_gemini_client(api_key_override: str | None = None):
    """Create a google.genai.Client using Vertex AI or AI Studio credentials."""
    from google import genai

    if api_key_override:
        return genai.Client(api_key=api_key_override)

    if GOOGLE_PROJECT_ID:
        _setup_vertex_credentials()
        return genai.Client(
            vertexai=True,
            project=GOOGLE_PROJECT_ID,
            location=GOOGLE_LOCATION,
        )

    # Re-read from env in case it was set after import (e.g. Streamlit sidebar)
    key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
    if key:
        return genai.Client(api_key=key)

    raise ValueError(
        "No Gemini credentials found. Set either GEMINI_API_KEY (AI Studio) "
        "or GOOGLE_PROJECT_ID + GOOGLE_SERVICE_ACCOUNT_JSON (Vertex AI)."
    )
