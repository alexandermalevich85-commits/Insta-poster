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


def git_pull():
    """Pull latest posts from GitHub."""
    try:
        result = subprocess.run(
            ["git", "pull", "--rebase"],
            cwd=BASE_DIR,
            capture_output=True, text=True, timeout=30,
        )
        if "Already up to date" not in result.stdout:
            log.info("Git pull: %s", result.stdout.strip())
        return True
    except Exception as e:
        log.warning("Git pull failed: %s", e)
        return False


def git_push():
    """Push updated posts to GitHub."""
    try:
        subprocess.run(
            ["git", "add", "pending_posts.json", "history.json"],
            cwd=BASE_DIR, capture_output=True, timeout=10,
        )
        result = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            cwd=BASE_DIR, capture_output=True, timeout=10,
        )
        if result.returncode != 0:  # there are changes
            subprocess.run(
                ["git", "commit", "-m", f"Auto-publish {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
                cwd=BASE_DIR, capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "push"],
                cwd=BASE_DIR, capture_output=True, timeout=30,
            )
            log.info("Pushed updated posts to GitHub")
    except Exception as e:
        log.warning("Git push failed: %s", e)


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
    """One full cycle: pull from GitHub + publish due posts + push results."""
    # Step 1: Pull latest posts from GitHub (generated by GitHub Actions)
    git_pull()

    # Step 2: Publish any due posts
    published = publish_due_posts()

    if published:
        log.info("Published %d post(s).", published)
        # Step 3: Push updated statuses to GitHub
        git_push()
    else:
        now = datetime.now().strftime("%H:%M")
        log.info("No posts due at %s.", now)


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

    # Full auto-pilot loop
    log.info("=" * 50)
    log.info("INSTA-POSTER AUTO-PILOT")
    log.info("Checking every %d minutes", interval_min)
    log.info("Press Ctrl+C to stop")
    log.info("=" * 50)

    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            break
        except Exception as e:
            log.error("Unexpected error: %s", e)

        log.info("Next check in %d min...\n", interval_min)
        try:
            time.sleep(interval_min * 60)
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            break


if __name__ == "__main__":
    main()
