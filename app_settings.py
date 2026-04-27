"""Runtime settings controlled via Streamlit UI.

Stored in app_settings.json (synced via GitHub). The CLI commands
(cmd_generate, cmd_publish_smart) and GitHub Actions workflows read
this file to know whether to run, how many posts per day, and at what
times to publish.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, date
from typing import TypedDict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "app_settings.json")


class AppSettings(TypedDict):
    auto_generate_enabled: bool
    auto_publish_enabled: bool
    posts_per_day: int
    publish_times: list[str]  # ["HH:MM", ...]


DEFAULTS: AppSettings = {
    "auto_generate_enabled": True,
    "auto_publish_enabled": True,
    "posts_per_day": 5,
    "publish_times": ["10:00", "13:00", "16:00", "19:00", "21:00"],
}


def load_settings() -> AppSettings:
    """Load settings, falling back to defaults for missing keys."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    merged: AppSettings = dict(DEFAULTS)  # type: ignore
    for k in DEFAULTS:
        if k in data:
            merged[k] = data[k]  # type: ignore
    return merged


def save_settings(settings: AppSettings) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def schedule_times_for_today(settings: AppSettings | None = None,
                             count: int | None = None,
                             timezone_str: str = "Europe/Moscow",
                             base_date: date | None = None) -> list[datetime]:
    """Convert publish_times ["HH:MM", ...] into timezone-aware datetimes for today.

    Returns the first `count` times from publish_times. If count > len(publish_times),
    extra times are extrapolated.
    """
    import pytz

    s = settings or load_settings()
    n = count if count is not None else s["posts_per_day"]
    tz = pytz.timezone(timezone_str)
    day = base_date or datetime.now(tz).date()

    times = []
    publish_times = s["publish_times"]

    for i in range(n):
        if i < len(publish_times):
            hh_mm = publish_times[i]
        else:
            # Fallback: extrapolate evenly between last time and 22:00
            hh_mm = "21:00"
        try:
            hh, mm = map(int, hh_mm.split(":"))
        except Exception:
            hh, mm = 12, 0
        t = tz.localize(datetime.combine(day, datetime.min.time().replace(hour=hh, minute=mm)))
        times.append(t)
    return sorted(times)
