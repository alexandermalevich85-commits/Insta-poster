#!/usr/bin/env python3
"""Full auto-pilot: generates posts for the day + publishes them on schedule.

Usage:
    python3 autopublish.py                # generate + publish loop (every 10 min)
    python3 autopublish.py --loop 5       # check every 5 minutes
    python3 autopublish.py --publish-only # only publish, don't generate
    python3 autopublish.py --generate-only # only generate, don't publish
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import logging
from datetime import datetime

import pytz

# Ensure project dir is in path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# Load .env file
_env_file = os.path.join(BASE_DIR, ".env")
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("autopublish")


def github_pull():
    """Pull latest data files from GitHub via API (no git conflicts)."""
    try:
        from github_sync import sync_data_from_github
        pulled = sync_data_from_github(BASE_DIR)
        if pulled:
            log.info("Pulled from GitHub: %s", ", ".join(pulled))
        return True
    except Exception as e:
        log.warning("GitHub pull failed: %s", e)
        return False


def github_push():
    """Push data files to GitHub via API (no git conflicts)."""
    try:
        from github_sync import sync_data_to_github
        msg = f"Auto-publish {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        pushed = sync_data_to_github(BASE_DIR, msg)
        if pushed:
            log.info("Pushed to GitHub: %s", ", ".join(pushed))
    except Exception as e:
        log.warning("GitHub push failed: %s", e)


def has_pending_for_today() -> bool:
    """Check if there are already pending posts scheduled for today."""
    from main import load_pending
    from config import TIMEZONE

    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).date()

    pending = load_pending()
    for post in pending:
        if post["status"] != "pending":
            continue
        scheduled = post.get("scheduled_at")
        if scheduled:
            scheduled_date = datetime.fromisoformat(scheduled).date()
            if scheduled_date == today:
                return True
    return False


def generate_daily_posts():
    """Generate posts for today if not already generated."""
    if has_pending_for_today():
        log.info("Posts for today already exist. Skipping generation.")
        return

    log.info("No posts for today. Generating...")
    from main import cmd_generate
    try:
        cmd_generate()
        log.info("Daily posts generated successfully!")
        # Push new posts to GitHub
        github_push()
    except Exception as e:
        log.error("Failed to generate posts: %s", e)


def publish_due_posts() -> int:
    """Find and publish all posts whose scheduled_at has passed."""
    from main import cmd_publish_next, load_pending

    published_count = 0
    max_per_run = 5  # safety limit

    for _ in range(max_per_run):
        # Check if there's anything due
        pending = load_pending()
        tz = pytz.timezone("Europe/Moscow")
        now = datetime.now(tz)

        has_due = False
        for post in pending:
            if post["status"] in ("pending", "retry") and post.get("scheduled_at"):
                scheduled_dt = datetime.fromisoformat(post["scheduled_at"])
                if scheduled_dt <= now:
                    has_due = True
                    break

        if not has_due:
            break

        try:
            cmd_publish_next()
            published_count += 1
            # Pause 1-5 minutes between posts (random)
            if published_count < max_per_run:
                import random
                pause = random.randint(60, 300)
                log.info("Waiting %d seconds before next post...", pause)
                time.sleep(pause)
        except Exception as e:
            log.error("Error publishing: %s", e)
            break

    return published_count


def run_cycle():
    """One full cycle: pull from GitHub + generate if needed + publish + push."""
    from config import TIMEZONE

    # Step 1: Pull latest data from GitHub via API (no git conflicts)
    github_pull()

    # Step 2: Fallback generation — only if GitHub Actions failed AND it's past 10:00 MSK
    # GitHub Actions generates at 05:00 MSK; if by 10:00 there are still no posts, generate locally.
    tz = pytz.timezone(TIMEZONE)
    now_local = datetime.now(tz)
    if now_local.hour >= 10 and not has_pending_for_today():
        log.warning("No posts for today and it's past 10:00 — GitHub Actions may have failed. Generating locally as fallback...")
        try:
            from main import cmd_generate
            cmd_generate()
            log.info("Fallback generation complete.")
            github_push()
        except Exception as e:
            log.error("Fallback generation failed: %s", e)

    # Step 3: Publish any due posts
    published = publish_due_posts()

    if published:
        log.info("Published %d post(s).", published)
        # Push updated statuses to GitHub via API
        github_push()
    else:
        log.info("No posts due at %s.", now_local.strftime("%H:%M"))


def main():
    args = sys.argv[1:]

    # Parse interval
    interval_min = 10
    if "--loop" in args:
        idx = args.index("--loop")
        if idx + 1 < len(args) and args[idx + 1].isdigit():
            interval_min = int(args[idx + 1])

    if "--generate-only" in args:
        generate_daily_posts()
        return

    if "--publish-only" in args:
        published = publish_due_posts()
        log.info("Published %d post(s).", published)
        return

    # Single run — LaunchAgent restarts every 10 min via StartInterval.
    # This ensures code changes take effect immediately on next invocation.
    try:
        run_cycle()
    except Exception as e:
        log.error("Unexpected error in run_cycle: %s", e)


if __name__ == "__main__":
    main()
