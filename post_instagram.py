"""Instagram carousel publishing via Graph API and instagrapi.

Supports three publishing modes:
- graph_api: Immediate publish via Graph API
- graph_api_scheduled: Schedule post for future publish_at time via Graph API
- instagrapi: Publish via unofficial instagrapi library
"""

from __future__ import annotations

import base64
import logging
import os
import time
from datetime import datetime, timezone

import requests

from config import (
    IG_ACCESS_TOKEN, IG_USER_ID, IG_PUBLISH_METHOD,
    INSTAGRAPI_USERNAME, INSTAGRAPI_PASSWORD, IMGBB_API_KEY,
)


def _env(key: str, fallback: str = "") -> str:
    """Re-read env var (handles Streamlit sidebar updates after import)."""
    return os.getenv(key, fallback)

log = logging.getLogger("insta-poster")

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

# Scheduled publishing constraints
MIN_SCHEDULE_SECONDS = 10 * 60       # 10 minutes in the future
MAX_SCHEDULE_SECONDS = 75 * 24 * 3600  # 75 days in the future


# ── Image hosting (required for Graph API) ───────────────────────────────────


def _upload_to_imgbb(image_path: str, api_key: str | None = None) -> str:
    """Upload image to imgbb.com and return public URL."""
    key = api_key or IMGBB_API_KEY or os.getenv("IMGBB_API_KEY", "")
    if not key:
        raise ValueError("IMGBB_API_KEY required for Graph API image hosting")

    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("ascii")

    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": key, "image": image_data, "expiration": 3600},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        raise RuntimeError(f"imgbb upload failed: {data}")

    return data["data"]["url"]


# ── Graph API ────────────────────────────────────────────────────────────────


def _create_media_container(
    image_url: str,
    is_carousel_item: bool = True,
    caption: str | None = None,
    access_token: str | None = None,
    ig_user_id: str | None = None,
) -> str:
    """Create a media container for a single image."""
    token = access_token or IG_ACCESS_TOKEN or _env("IG_ACCESS_TOKEN")
    user_id = ig_user_id or IG_USER_ID or _env("IG_USER_ID")

    params = {
        "image_url": image_url,
        "media_type": "IMAGE",
        "is_carousel_item": str(is_carousel_item).lower(),
        "access_token": token,
    }
    if caption and not is_carousel_item:
        params["caption"] = caption

    resp = requests.post(f"{GRAPH_API_BASE}/{user_id}/media", data=params, timeout=60)
    if resp.status_code != 200:
        error_detail = resp.text
        try:
            error_detail = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            pass
        raise RuntimeError(f"Instagram API error ({resp.status_code}): {error_detail}")
    data = resp.json()

    container_id = data.get("id")
    if not container_id:
        raise RuntimeError(f"Failed to create media container: {data}")

    log.info("Created media container: %s", container_id)
    return container_id


def _create_carousel_container(
    children_ids: list[str],
    caption: str,
    access_token: str | None = None,
    ig_user_id: str | None = None,
    publish_at: int | None = None,
) -> str:
    """Create a carousel container from child media containers.

    Args:
        publish_at: Unix timestamp for scheduled publishing (Creator/Business accounts).
                    Must be 10 min to 75 days in the future. If set, the post will be
                    auto-published at that time without calling media_publish.
    """
    token = access_token or IG_ACCESS_TOKEN or _env("IG_ACCESS_TOKEN")
    user_id = ig_user_id or IG_USER_ID or _env("IG_USER_ID")

    params = {
        "media_type": "CAROUSEL",
        "children": ",".join(children_ids),
        "caption": caption,
        "access_token": token,
    }

    if publish_at is not None:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        delta = publish_at - now_ts
        if delta < MIN_SCHEDULE_SECONDS:
            raise ValueError(
                f"publish_at must be at least 10 minutes in the future "
                f"(got {delta}s from now)"
            )
        if delta > MAX_SCHEDULE_SECONDS:
            raise ValueError(
                f"publish_at must be within 75 days "
                f"(got {delta // 86400} days from now)"
            )
        params["publish_at"] = str(publish_at)

    resp = requests.post(f"{GRAPH_API_BASE}/{user_id}/media", data=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    container_id = data.get("id")
    if not container_id:
        raise RuntimeError(f"Failed to create carousel container: {data}")

    log.info("Created carousel container: %s (publish_at=%s)", container_id, publish_at)
    return container_id


def _publish_container(
    container_id: str,
    access_token: str | None = None,
    ig_user_id: str | None = None,
) -> str:
    """Publish a media container."""
    token = access_token or IG_ACCESS_TOKEN or _env("IG_ACCESS_TOKEN")
    user_id = ig_user_id or IG_USER_ID or _env("IG_USER_ID")

    # Wait for container to be ready (Instagram needs processing time)
    for attempt in range(10):
        resp = requests.get(
            f"{GRAPH_API_BASE}/{container_id}",
            params={"fields": "status_code", "access_token": token},
            timeout=30,
        )
        resp.raise_for_status()
        status = resp.json().get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError(f"Container processing failed: {resp.json()}")
        log.info("Container status: %s, waiting... (attempt %d)", status, attempt + 1)
        time.sleep(5)

    params = {"creation_id": container_id, "access_token": token}
    resp = requests.post(f"{GRAPH_API_BASE}/{user_id}/media_publish", data=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    media_id = data.get("id")
    if not media_id:
        raise RuntimeError(f"Failed to publish: {data}")

    log.info("Published carousel: media_id=%s", media_id)
    return media_id


def publish_carousel_graph_api(
    image_paths: list[str],
    caption: str,
    access_token: str | None = None,
    ig_user_id: str | None = None,
    imgbb_api_key: str | None = None,
) -> dict:
    """Publish a carousel to Instagram via Graph API.

    Steps:
    1. Upload each image to imgbb (public URL required by Graph API)
    2. Create a media container for each image
    3. Create a carousel container with all children
    4. Publish the carousel

    Returns:
        {"ok": True, "media_id": "...", "method": "graph_api"}
    """
    # Step 1: Upload images to public hosting
    image_urls = []
    for i, path in enumerate(image_paths):
        log.info("Uploading slide %d/%d to imgbb...", i + 1, len(image_paths))
        url = _upload_to_imgbb(path, api_key=imgbb_api_key)
        image_urls.append(url)
        time.sleep(1)  # Rate limit

    # Step 2: Create child containers
    children_ids = []
    for i, url in enumerate(image_urls):
        log.info("Creating container %d/%d...", i + 1, len(image_urls))
        container_id = _create_media_container(
            image_url=url,
            is_carousel_item=True,
            access_token=access_token,
            ig_user_id=ig_user_id,
        )
        children_ids.append(container_id)
        time.sleep(1)

    # Step 3: Create carousel container
    carousel_id = _create_carousel_container(
        children_ids=children_ids,
        caption=caption,
        access_token=access_token,
        ig_user_id=ig_user_id,
    )

    # Step 4: Publish
    time.sleep(3)  # Wait for processing
    media_id = _publish_container(
        container_id=carousel_id,
        access_token=access_token,
        ig_user_id=ig_user_id,
    )

    return {"ok": True, "media_id": media_id, "method": "graph_api"}


# ── Graph API Scheduled Publishing ──────────────────────────────────────────


def schedule_carousel_graph_api(
    image_paths: list[str],
    caption: str,
    publish_at: int | datetime,
    access_token: str | None = None,
    ig_user_id: str | None = None,
    imgbb_api_key: str | None = None,
) -> dict:
    """Schedule a carousel for future publishing via Graph API.

    Uses the Content Publishing API's publish_at parameter to schedule
    the post. Available for Creator and Business accounts.

    Args:
        image_paths: List of slide image paths.
        caption: Instagram caption text.
        publish_at: Unix timestamp (int) or datetime for scheduled publish time.
                    Must be 10 min to 75 days in the future.

    Returns:
        {"ok": True, "container_id": "...", "publish_at": ..., "method": "graph_api_scheduled"}
    """
    # Convert datetime to Unix timestamp
    if isinstance(publish_at, datetime):
        if publish_at.tzinfo is None:
            raise ValueError("publish_at datetime must be timezone-aware")
        publish_ts = int(publish_at.timestamp())
    else:
        publish_ts = int(publish_at)

    # Step 1: Upload images to public hosting
    image_urls = []
    for i, path in enumerate(image_paths):
        log.info("Uploading slide %d/%d to imgbb...", i + 1, len(image_paths))
        url = _upload_to_imgbb(path, api_key=imgbb_api_key)
        image_urls.append(url)
        time.sleep(1)

    # Step 2: Create child containers
    children_ids = []
    for i, url in enumerate(image_urls):
        log.info("Creating container %d/%d...", i + 1, len(image_urls))
        container_id = _create_media_container(
            image_url=url,
            is_carousel_item=True,
            access_token=access_token,
            ig_user_id=ig_user_id,
        )
        children_ids.append(container_id)
        time.sleep(1)

    # Step 3: Create carousel container with publish_at (no need to call media_publish)
    carousel_container_id = _create_carousel_container(
        children_ids=children_ids,
        caption=caption,
        access_token=access_token,
        ig_user_id=ig_user_id,
        publish_at=publish_ts,
    )

    scheduled_dt = datetime.fromtimestamp(publish_ts, tz=timezone.utc)
    log.info(
        "Scheduled carousel %s for %s",
        carousel_container_id,
        scheduled_dt.isoformat(),
    )

    return {
        "ok": True,
        "container_id": carousel_container_id,
        "publish_at": publish_ts,
        "publish_at_iso": scheduled_dt.isoformat(),
        "method": "graph_api_scheduled",
    }


# ── instagrapi (fallback) ────────────────────────────────────────────────────


def publish_carousel_instagrapi(
    image_paths: list[str],
    caption: str,
    username: str | None = None,
    password: str | None = None,
) -> dict:
    """Publish a carousel via instagrapi (unofficial Instagram API).

    Returns:
        {"ok": True, "media_id": "...", "media_code": "...", "method": "instagrapi"}
    """
    from instagrapi import Client
    from pathlib import Path

    user = username or INSTAGRAPI_USERNAME or _env("INSTAGRAPI_USERNAME")
    pwd = password or INSTAGRAPI_PASSWORD or _env("INSTAGRAPI_PASSWORD")

    if not user or not pwd:
        raise ValueError("INSTAGRAPI_USERNAME and INSTAGRAPI_PASSWORD required")

    # Session file to avoid re-login challenges
    _base = os.path.dirname(os.path.abspath(__file__))
    session_file = os.path.join(_base, f"ig_session_{user}.json")

    cl = Client()
    cl.delay_range = [2, 5]

    # Try to load saved session first
    if os.path.exists(session_file):
        try:
            cl.load_settings(session_file)
            cl.login(user, pwd)
            cl.get_timeline_feed()  # verify session works
            log.info("Logged in with saved session")
        except Exception:
            log.warning("Saved session expired, logging in fresh...")
            cl = Client()
            cl.delay_range = [2, 5]
            cl.login(user, pwd)
            cl.dump_settings(session_file)
    else:
        cl.login(user, pwd)
        cl.dump_settings(session_file)

    paths = [Path(p) for p in image_paths]
    media = cl.album_upload(paths, caption=caption)

    log.info("Published carousel via instagrapi: media_id=%s, code=%s", media.id, media.code)

    return {
        "ok": True,
        "media_id": str(media.id),
        "media_code": media.code,
        "method": "instagrapi",
    }


# ── Unified entry point ─────────────────────────────────────────────────────


def publish_carousel(
    image_paths: list[str],
    caption: str,
    method: str | None = None,
    publish_at: int | datetime | None = None,
    **kwargs,
) -> dict:
    """Publish or schedule a carousel using configured method.

    Args:
        image_paths: List of 8 slide image paths.
        caption: Instagram caption text.
        method: Override IG_PUBLISH_METHOD (graph_api | graph_api_scheduled | instagrapi).
        publish_at: Unix timestamp or datetime for scheduled publishing
                    (only used with graph_api_scheduled method).

    Returns:
        {"ok": True, "media_id"|"container_id": "...", "method": "..."}
    """
    m = (method or IG_PUBLISH_METHOD or _env("IG_PUBLISH_METHOD", "instagrapi")).lower()

    if m == "graph_api":
        return publish_carousel_graph_api(image_paths, caption, **kwargs)
    elif m == "graph_api_scheduled":
        if publish_at is None:
            raise ValueError("publish_at is required for graph_api_scheduled method")
        return schedule_carousel_graph_api(image_paths, caption, publish_at=publish_at, **kwargs)
    elif m == "instagrapi":
        return publish_carousel_instagrapi(image_paths, caption, **kwargs)
    else:
        raise ValueError(
            f"Unknown publish method: '{m}'. "
            "Use 'graph_api', 'graph_api_scheduled', or 'instagrapi'."
        )
