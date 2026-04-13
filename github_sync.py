"""GitHub API sync helpers — no git commands, no conflicts."""

from __future__ import annotations

import base64
import json
import logging
import os

import requests

log = logging.getLogger("insta-poster")

GITHUB_REPO = "alexandermalevich85-commits/Insta-poster"


def _get_github_token() -> str | None:
    """Get GitHub token from env or .env file."""
    token = os.getenv("GITHUB_TOKEN")
    if token:
        return token
    # Try Streamlit secrets
    try:
        import streamlit as st
        return st.secrets.get("GITHUB_TOKEN")
    except Exception:
        pass
    return None


def push_file(file_path: str, commit_message: str) -> bool:
    """Push a single file to GitHub repository via API."""
    token = _get_github_token()
    if not token:
        log.warning("GITHUB_TOKEN not found, skipping push")
        return False

    filename = os.path.basename(file_path)
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        # Get current file SHA (needed for update)
        resp = requests.get(api_url, headers=headers, timeout=10)
        sha = resp.json().get("sha") if resp.status_code == 200 else None
    except requests.ConnectionError:
        log.warning("No internet connection, skipping push of %s", filename)
        return False

    # Read file content
    with open(file_path, "r", encoding="utf-8") as f:
        content = base64.b64encode(f.read().encode()).decode()

    data = {
        "message": commit_message,
        "content": content,
    }
    if sha:
        data["sha"] = sha

    try:
        resp = requests.put(api_url, headers=headers, json=data, timeout=10)
    except requests.ConnectionError:
        log.warning("No internet connection, skipping push of %s", filename)
        return False

    if resp.status_code in (200, 201):
        log.info("Pushed %s to GitHub", filename)
        return True
    else:
        log.warning("Failed to push %s: %s %s", filename, resp.status_code, resp.text[:200])
        return False


def pull_file(filename: str, local_path: str) -> bool:
    """Pull a file from GitHub repository via API."""
    token = _get_github_token()
    if not token:
        log.warning("GITHUB_TOKEN not found, skipping pull")
        return False

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
    except requests.ConnectionError:
        log.warning("No internet connection, skipping pull of %s", filename)
        return False

    if resp.status_code != 200:
        log.warning("Failed to pull %s: %s", filename, resp.status_code)
        return False

    content = base64.b64decode(resp.json()["content"]).decode()
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(content)
    log.info("Pulled %s from GitHub", filename)
    return True


def sync_data_to_github(base_dir: str, message: str = "Auto-publish") -> list[str]:
    """Push pending_posts.json and history.json to GitHub via API."""
    pushed = []
    for filename in ("pending_posts.json", "history.json"):
        path = os.path.join(base_dir, filename)
        if os.path.exists(path) and push_file(path, message):
            pushed.append(filename)
    return pushed


def sync_data_from_github(base_dir: str) -> list[str]:
    """Pull pending_posts.json, ideas.json, history.json from GitHub via API."""
    pulled = []
    for filename in ("pending_posts.json", "ideas.json", "history.json", "prompts.json"):
        path = os.path.join(base_dir, filename)
        if pull_file(filename, path):
            pulled.append(filename)
    return pulled
