"""Shared utility functions."""

from __future__ import annotations

import atexit
import base64
import io
import os
import random
import tempfile
import time
import logging
from datetime import date, datetime, timedelta

from PIL import Image

log = logging.getLogger("insta-poster")


# ── Image encoding ───────────────────────────────────────────────────────────


def image_to_base64(image_path: str) -> str:
    """Read image, compress to JPEG quality 85, return base64 string."""
    img = Image.open(image_path)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


_temp_files: list[str] = []


def _register_temp(path: str) -> str:
    """Register a temp file for cleanup on process exit."""
    _temp_files.append(path)
    return path


def _cleanup_temp_files():
    """Remove all registered temporary files."""
    for p in _temp_files:
        try:
            if os.path.exists(p):
                os.unlink(p)
        except OSError:
            pass
    _temp_files.clear()


atexit.register(_cleanup_temp_files)


def base64_to_tempfile(b64_string: str) -> str:
    """Decode base64 to a temporary JPEG file, return its path."""
    data = base64.b64decode(b64_string)
    fd, path = tempfile.mkstemp(suffix=".jpg", prefix="instaposter_")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(data)
    return _register_temp(path)


def detect_content_type(file_path: str) -> str:
    """Detect MIME content type from file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")


# ── Retry helper ─────────────────────────────────────────────────────────────


def retry(fn, max_attempts: int = 3, delay: float = 2.0, backoff: float = 2.0):
    """Call fn() with exponential backoff retries."""
    last_exc = None
    current_delay = delay
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < max_attempts:
                log.warning(
                    "Attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt, max_attempts, e, current_delay,
                )
                time.sleep(current_delay)
                current_delay *= backoff
            else:
                log.error("All %d attempts failed: %s", max_attempts, e)
    raise last_exc


# ── Schedule helpers ─────────────────────────────────────────────────────────


def calculate_schedule_times(
    count: int = 20,
    start_hour: int = 8,
    end_hour: int = 22,
    timezone_str: str = "Europe/Moscow",
    base_date: date | None = None,
) -> list[datetime]:
    """Calculate evenly-spaced publish times across the day.

    Returns timezone-aware datetimes with random jitter of ±5 minutes.
    """
    import pytz

    tz = pytz.timezone(timezone_str)
    today = base_date or date.today()

    start = tz.localize(datetime.combine(today, datetime.min.time().replace(hour=start_hour)))
    end = tz.localize(datetime.combine(today, datetime.min.time().replace(hour=end_hour)))

    total_minutes = (end - start).total_seconds() / 60
    if count == 0:
        return []
    interval = total_minutes / count

    times = []
    for i in range(count):
        offset_minutes = interval * i
        jitter = random.randint(-5, 5)
        offset_minutes = max(0, min(total_minutes, offset_minutes + jitter))
        t = start + timedelta(minutes=offset_minutes)
        times.append(t)

    return sorted(times)


def ensure_dir(path: str) -> str:
    """Create directory if it doesn't exist, return path."""
    os.makedirs(path, exist_ok=True)
    return path
