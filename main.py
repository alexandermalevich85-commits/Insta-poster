"""CLI entry point for Instagram carousel auto-poster."""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta

import pytz

from config import (
    TEXT_PROVIDER, TEXT_MODEL, POSTS_PER_DAY,
    PUBLISH_START_HOUR, PUBLISH_END_HOUR, TIMEZONE,
)

log = logging.getLogger("insta-poster")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IDEAS_FILE = os.path.join(BASE_DIR, "ideas.json")
PENDING_FILE = os.path.join(BASE_DIR, "pending_posts.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
PROMPTS_FILE = os.path.join(BASE_DIR, "prompts.json")
TMP_DIR = os.path.join(BASE_DIR, "tmp")


# ── JSON helpers ─────────────────────────────────────────────────────────────


def load_json(path: str, default=None):
    """Load JSON file, return default if not found."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else []


def save_json(path: str, data):
    """Save data to JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_prompts() -> dict:
    """Load prompts configuration."""
    return load_json(PROMPTS_FILE, {})


# ── Idea management ──────────────────────────────────────────────────────────


def load_ideas() -> list[dict]:
    return load_json(IDEAS_FILE, [])


def save_ideas(ideas: list[dict]):
    save_json(IDEAS_FILE, ideas)


def get_unused_ideas(ideas: list[dict], count: int) -> list[tuple[int, str]]:
    """Get up to `count` unused ideas. Returns list of (index, idea_text)."""
    result = []
    for i, item in enumerate(ideas):
        if not item.get("used", False):
            result.append((i, item["idea"]))
            if len(result) >= count:
                break
    return result


def mark_ideas_used(ideas: list[dict], indices: list[int]):
    """Mark ideas at given indices as used."""
    for idx in indices:
        if 0 <= idx < len(ideas):
            ideas[idx]["used"] = True


# ── Pending posts ────────────────────────────────────────────────────────────


def load_pending() -> list[dict]:
    return load_json(PENDING_FILE, [])


def save_pending(posts: list[dict]):
    save_json(PENDING_FILE, posts)


# ── History ──────────────────────────────────────────────────────────────────


def load_history() -> list[dict]:
    return load_json(HISTORY_FILE, [])


def save_history(history: list[dict]):
    save_json(HISTORY_FILE, history)


def add_history_entry(carousel: dict, media_id: str | None = None):
    """Move published carousel to history."""
    history = load_history()
    history.append({
        "date": datetime.now().isoformat(),
        "id": carousel.get("id", ""),
        "headline": carousel.get("headline", ""),
        "topic": carousel.get("topic", ""),
        "caption": carousel.get("caption", ""),
        "text_provider": carousel.get("text_provider", ""),
        "ig_media_id": media_id,
    })
    save_history(history)


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_generate():
    """Generate batch of carousel texts and schedule them."""
    from generate_text import generate_carousel_batch, generate_ideas
    from app_settings import load_settings, schedule_times_for_today

    prompts = load_prompts()
    system_prompt = prompts.get("system_prompt")
    ideas_prompt = prompts.get("ideas_prompt")
    target_audience = prompts.get("target_audience")
    cta_options = prompts.get("cta_options", [])

    settings = load_settings()
    if not settings["auto_generate_enabled"]:
        log.info("Auto-generation disabled in app_settings.json. Skipping.")
        print("Auto-generation disabled. Skipping.")
        return

    count = settings["posts_per_day"]
    ideas = load_ideas()

    # Get unused ideas
    unused = get_unused_ideas(ideas, count)

    # Auto-generate ideas if not enough
    if len(unused) < count:
        needed = count - len(unused)
        log.info("Only %d unused ideas, generating %d more...", len(unused), needed)
        existing = [item["idea"] for item in ideas]
        new_ideas = generate_ideas(
            count=needed,
            existing_ideas=existing,
            ideas_prompt=ideas_prompt,
            target_audience=target_audience,
        )
        for idea_text in new_ideas:
            ideas.append({"idea": idea_text, "used": False})
        save_ideas(ideas)
        unused = get_unused_ideas(ideas, count)

    if not unused:
        log.error("No ideas available for generation")
        return

    # Pause after ideas generation to respect Gemini RPM limit
    import time as _time
    _time.sleep(60)

    # Generate carousels
    idea_texts = [text for _, text in unused]
    idea_indices = [idx for idx, _ in unused]

    carousels = generate_carousel_batch(
        ideas=idea_texts,
        count=count,
        system_prompt=system_prompt,
        cta_options=cta_options,
    )

    # Assign schedule times for TOMORROW (day-ahead buffer):
    # We generate today, IG publishes tomorrow at the configured slot times.
    import pytz as _pytz
    _tz = _pytz.timezone(TIMEZONE)
    _tomorrow = (datetime.now(_tz) + timedelta(days=1)).date()
    schedule_times = schedule_times_for_today(
        settings=settings,
        count=len(carousels),
        timezone_str=TIMEZONE,
        base_date=_tomorrow,
    )

    # Build pending posts
    pending = load_pending()
    used_indices = []

    for i, carousel in enumerate(carousels):
        carousel_id = str(uuid.uuid4())[:8]
        post = {
            "id": carousel_id,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "scheduled_at": schedule_times[i].isoformat() if i < len(schedule_times) else None,
            "topic": carousel.get("topic", ""),
            "headline": carousel["headline"],
            "points": carousel["points"],
            "cta_keyword": carousel.get("cta_keyword", ""),
            "cta_text": carousel.get("cta_text", ""),
            "ps_text": carousel.get("ps_text", ""),
            "caption": _build_caption(carousel),
            "text_provider": TEXT_PROVIDER,
            "idea": carousel.get("idea", ""),
            "published_at": None,
            "ig_media_id": None,
        }
        pending.append(post)
        if i < len(idea_indices):
            used_indices.append(idea_indices[i])

    save_pending(pending)
    mark_ideas_used(ideas, used_indices)
    save_ideas(ideas)

    log.info("Generated %d carousels, saved to pending_posts.json", len(carousels))
    print(f"Generated {len(carousels)} carousels.")
    for i, c in enumerate(carousels):
        time_str = schedule_times[i].strftime("%H:%M") if i < len(schedule_times) else "?"
        print(f"  {i+1}. [{time_str}] {c['headline'][:60]}...")


def cmd_publish_next():
    """Publish the next carousel whose scheduled time has arrived."""
    from render_slides import render_carousel
    from post_instagram import publish_carousel
    from utils import ensure_dir

    pending = load_pending()
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)

    # Find next due carousel
    target = None
    target_idx = None
    for i, post in enumerate(pending):
        if post["status"] not in ("pending", "retry"):
            continue
        scheduled = post.get("scheduled_at")
        if not scheduled:
            continue
        scheduled_dt = datetime.fromisoformat(scheduled)
        if scheduled_dt <= now:
            target = post
            target_idx = i
            break

    if target is None:
        log.info("No carousels due for publishing right now.")
        print("No carousels due for publishing.")
        return

    log.info("Publishing carousel: %s — %s", target["id"], target["headline"][:50])
    print(f"Publishing: {target['headline'][:60]}...")

    # Render slides
    output_dir = ensure_dir(os.path.join(TMP_DIR, target["id"]))
    image_paths = render_carousel(target, output_dir)
    log.info("Rendered %d slides", len(image_paths))

    # Publish
    try:
        result = publish_carousel(
            image_paths, target.get("caption", ""),
            method=os.getenv("IG_PUBLISH_METHOD"),
        )
        media_id = result.get("media_id")

        # Update pending
        pending[target_idx]["status"] = "published"
        pending[target_idx]["published_at"] = datetime.now().isoformat()
        pending[target_idx]["ig_media_id"] = media_id
        save_pending(pending)

        # Add to history
        add_history_entry(target, media_id)

        log.info("Published successfully: media_id=%s", media_id)
        print(f"Published! media_id={media_id}")

    except Exception as e:
        pending[target_idx]["status"] = "failed"
        pending[target_idx]["error"] = str(e)
        save_pending(pending)
        log.error("Failed to publish: %s", e)
        print(f"Failed to publish: {e}")
        raise


def cmd_publish():
    """Publish all pending carousels whose time has arrived.

    Hard cap: max 5 posts per day to avoid IG spam flags.
    """
    from utils import retry
    import time

    MAX_PER_DAY = 5

    pending = load_pending()
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    today = now.date()

    # Count how many we already published today
    published_today = sum(
        1 for p in pending
        if p["status"] == "published"
        and p.get("published_at")
        and datetime.fromisoformat(p["published_at"]).date() == today
    )

    remaining_today = max(0, MAX_PER_DAY - published_today)
    if remaining_today == 0:
        print(f"Daily limit reached ({MAX_PER_DAY} posts). Skipping.")
        log.info("Daily publish limit reached (%d).", MAX_PER_DAY)
        return

    due_count = sum(
        1 for p in pending
        if p["status"] == "pending"
        and p.get("scheduled_at")
        and datetime.fromisoformat(p["scheduled_at"]) <= now
    )

    if due_count == 0:
        print("No carousels due for publishing.")
        return

    to_publish = min(due_count, remaining_today)
    print(f"Found {due_count} due carousels. Publishing {to_publish} (cap: {MAX_PER_DAY}/day, already published today: {published_today}).")

    for i in range(to_publish):
        try:
            cmd_publish_next()
        except Exception as e:
            log.error("Error publishing carousel %d: %s", i + 1, e)
        time.sleep(5)  # Delay between publishes


def cmd_cleanup_stale():
    """Mark long-overdue pending posts as 'expired' so they don't pile up.

    Posts whose scheduled_at is more than 1 day in the past — we'll never
    publish them retroactively. Better to drop them than have a 150-deep queue.
    """
    pending = load_pending()
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    cutoff = now - timedelta(days=1)

    expired = 0
    for p in pending:
        if p["status"] != "pending":
            continue
        scheduled = p.get("scheduled_at")
        if not scheduled:
            continue
        sched_dt = datetime.fromisoformat(scheduled)
        if sched_dt.tzinfo is None:
            sched_dt = tz.localize(sched_dt)
        if sched_dt < cutoff:
            p["status"] = "expired"
            expired += 1

    if expired:
        save_pending(pending)
        log.info("Marked %d stale posts as expired.", expired)
        print(f"Cleanup: marked {expired} stale posts as expired.")
    else:
        print("Cleanup: nothing to expire.")


def cmd_publish_smart():
    """Smart publishing: publish 1 immediately, schedule the rest via publish_at.

    Strategy:
    1. Cleanup stale (>1 day past-due) posts.
    2. Take today's pending posts (sorted by scheduled_at).
    3. First post (closest scheduled_at) -> publish immediately.
    4. Rest of today's posts -> schedule via Graph API publish_at.
       Instagram will auto-publish them at their scheduled times.
    5. Cap: 5 per day total.
    """
    from render_slides import render_carousel
    from post_instagram import publish_carousel
    from utils import ensure_dir
    from app_settings import load_settings

    settings = load_settings()
    if not settings["auto_publish_enabled"]:
        log.info("Auto-publish disabled in app_settings.json. Skipping.")
        print("Auto-publish disabled. Skipping.")
        return

    MAX_PER_DAY = settings["posts_per_day"]

    cmd_cleanup_stale()

    pending = load_pending()
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    today = now.date()

    # How many already done (published or scheduled) today
    done_today = sum(
        1 for p in pending
        if p["status"] in ("published", "scheduled")
        and (
            (p.get("published_at") and datetime.fromisoformat(p["published_at"]).date() == today)
            or (p.get("scheduled_at") and datetime.fromisoformat(p["scheduled_at"]).date() == today)
        )
    )
    remaining = max(0, MAX_PER_DAY - done_today)

    if remaining == 0:
        print(f"Daily limit reached ({MAX_PER_DAY}). Skipping.")
        return

    # Today's pending posts, sorted by scheduled_at
    todays = []
    for i, p in enumerate(pending):
        if p["status"] != "pending":
            continue
        sched = p.get("scheduled_at")
        if not sched:
            continue
        sched_dt = datetime.fromisoformat(sched)
        if sched_dt.tzinfo is None:
            sched_dt = tz.localize(sched_dt)
        if sched_dt.date() != today:
            continue
        todays.append((i, p, sched_dt))
    todays.sort(key=lambda x: x[2])
    todays = todays[:remaining]

    if not todays:
        print("No pending posts for today.")
        return

    print(f"Today's plan: {len(todays)} post(s). 1 immediate + {len(todays)-1} scheduled.")

    for n, (idx, post, sched_dt) in enumerate(todays):
        is_first = (n == 0)
        output_dir = ensure_dir(os.path.join(TMP_DIR, post["id"]))

        try:
            image_paths = render_carousel(post, output_dir)

            if is_first:
                # Immediate publish
                log.info("Publishing immediately: %s", post["id"])
                print(f"  [now] {post['headline'][:60]}...")
                result = publish_carousel(
                    image_paths, post.get("caption", ""), method="graph_api",
                )
                pending[idx]["status"] = "published"
                pending[idx]["published_at"] = datetime.now().isoformat()
                pending[idx]["ig_media_id"] = result.get("media_id")
                add_history_entry(post, result.get("media_id"))
                print(f"    Published! media_id={result.get('media_id')}")
            else:
                # Schedule via publish_at — IG auto-publishes at this time
                # Must be at least 10 minutes in the future
                min_future = now + timedelta(minutes=15)
                effective_sched = max(sched_dt, min_future)
                log.info("Scheduling: %s at %s", post["id"], effective_sched.isoformat())
                print(f"  [{effective_sched.strftime('%H:%M')}] {post['headline'][:60]}...")
                result = publish_carousel(
                    image_paths, post.get("caption", ""),
                    method="graph_api_scheduled",
                    publish_at=effective_sched,
                )
                pending[idx]["status"] = "scheduled"
                pending[idx]["ig_container_id"] = result.get("container_id")
                pending[idx]["publish_at_ts"] = result.get("publish_at")
                print(f"    Scheduled at {effective_sched.strftime('%H:%M')}")

            save_pending(pending)
        except Exception as e:
            log.error("Failed for %s: %s", post["id"], e)
            print(f"    ERROR: {e}")
            pending[idx]["last_error"] = str(e)
            pending[idx]["last_error_at"] = datetime.now().isoformat()
            save_pending(pending)

        import time
        time.sleep(3)


def cmd_schedule():
    """Schedule all pending carousels via Instagram Scheduled Publishing API.

    Renders slides and creates scheduled containers for each pending post.
    Instagram will auto-publish them at their scheduled_at times.
    No cron job needed for publishing.
    """
    from render_slides import render_carousel
    from post_instagram import publish_carousel
    from utils import ensure_dir

    pending = load_pending()
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)

    to_schedule = [
        (i, p) for i, p in enumerate(pending)
        if p["status"] == "pending" and p.get("scheduled_at")
    ]

    if not to_schedule:
        print("No pending carousels to schedule.")
        return

    print(f"Scheduling {len(to_schedule)} carousels via API...")

    scheduled_count = 0
    for i, (idx, post) in enumerate(to_schedule):
        scheduled_dt = datetime.fromisoformat(post["scheduled_at"])
        # Ensure timezone-aware
        if scheduled_dt.tzinfo is None:
            scheduled_dt = tz.localize(scheduled_dt)

        # Skip if scheduled time is in the past
        if scheduled_dt <= now:
            log.warning("Skipping %s — scheduled time %s is in the past", post["id"], scheduled_dt)
            continue

        log.info(
            "Scheduling %d/%d: %s at %s",
            i + 1, len(to_schedule), post["id"], scheduled_dt.strftime("%H:%M"),
        )
        print(f"  [{i+1}/{len(to_schedule)}] {scheduled_dt.strftime('%H:%M')} — {post['headline'][:50]}...")

        # Render slides
        output_dir = ensure_dir(os.path.join(TMP_DIR, post["id"]))
        image_paths = render_carousel(post, output_dir)

        try:
            result = publish_carousel(
                image_paths,
                post.get("caption", ""),
                method="graph_api_scheduled",
                publish_at=scheduled_dt,
            )

            pending[idx]["status"] = "scheduled"
            pending[idx]["ig_container_id"] = result.get("container_id")
            pending[idx]["publish_at_ts"] = result.get("publish_at")
            save_pending(pending)

            scheduled_count += 1
            log.info("Scheduled: container_id=%s", result.get("container_id"))

        except Exception as e:
            pending[idx]["status"] = "schedule_failed"
            pending[idx]["error"] = str(e)
            save_pending(pending)
            log.error("Failed to schedule %s: %s", post["id"], e)
            print(f"    ERROR: {e}")

        import time
        time.sleep(3)  # Rate limit between API calls

    print(f"\nScheduled {scheduled_count}/{len(to_schedule)} carousels.")


def cmd_generate_and_schedule():
    """Generate batch + schedule all via API in one step."""
    cmd_generate()
    cmd_schedule()


def cmd_full():
    """Generate + publish pipeline (immediate publishing)."""
    cmd_generate()
    cmd_publish()


def cmd_render_test():
    """Render a test carousel for visual inspection."""
    from render_slides import render_carousel
    from utils import ensure_dir

    test_carousel = {
        "id": "test",
        "headline": "Ваш НОС растет каждый год — это старит на 10 лет СРАЗУ.\nСекрет молодости НЕ в пластике\n(врачи молчат)",
        "points": [
            "1.Смотришь на старые фото и видишь, что нос стал шире и длиннее. «Я становлюсь похожа на бабу Ягу», — думаешь ты втайне от всех. Это не кости растут, а хрящи теряют опору и опускаются вниз.",
            "2.С возрастом связки носа слабеют, и кончик начинает предательски «клевать» вниз. «Мой нос стал каким-то мясистым и грубым». Это меняет пропорции лица, делая взгляд тяжелым, а лицо визуально более старым.",
            "3.Секрет молодости — в укреплении мышц, поднимающих кончик носа. «Я начала делать упражнение, и нос подтянулся». Это возвращает лицу легкость и девичьи пропорции без всякого вмешательства хирургов и боли.",
            "4.Чем меньше ты обращаешь внимания на свой нос, тем больше он опускается. Мышцы лица нуждаются в тренировке так же, как и твое тело. Парадокс: работа с носом делает глаза визуально больше и ярче.",
            "5.Начни делать легкое упражнение на поднятие кончика носа по 1 минуте в день. Это сохранит твои пропорции лица и не даст носу «состариться» раньше времени. А ты понимаешь, что твой нос меняет всё твое лицо?",
        ],
        "cta_text": "Напиши «КРАСОТА» в комменты — поделюсь своей системой естественного омоложения изнутри без дорогой косметики.",
        "ps_text": "Возможно, мы больше никогда с тобой не увидимся - подпи sывайся @lana_surskaya и поделиться с подружкой",
    }

    output_dir = ensure_dir(os.path.join(TMP_DIR, "render_test"))
    paths = render_carousel(test_carousel, output_dir)
    print(f"Rendered {len(paths)} slides to: {output_dir}")
    for p in paths:
        print(f"  {os.path.basename(p)}")


# ── Caption builder ──────────────────────────────────────────────────────────


def _build_caption(carousel: dict) -> str:
    """Build Instagram caption from carousel data — only CTA + P.S."""
    lines = []
    cta_keyword = carousel.get("cta_keyword", "")
    cta_text = carousel.get("cta_text", "")
    ps_text = carousel.get("ps_text", "")

    if cta_keyword and cta_text:
        lines.append(f"‼️Напиши «{cta_keyword}» в комменты")
        # cta_text may already contain the keyword intro — use the part after it
        # Extract just the action/benefit part
        cta_body = cta_text
        # Remove "Напиши «X» в комменты" prefix if present
        import re as _re
        # Remove "Напиши «X» в комменты" anywhere in text
        pattern = _re.compile(r'[Нн]апиши\s+[«"\']?' + _re.escape(cta_keyword) + r'[»"\']?\s+в комменты\s*', _re.IGNORECASE)
        cta_body = pattern.sub('', cta_body).strip(" —-–,.")
        if cta_body:
            lines.append(f"- {cta_body}")
    elif cta_text:
        lines.append(cta_text)

    lines.append("")
    lines.append("P.s.: Возможно, мы больше никогда \nс тобой не увидимся - не забудь подпи sаtся ✏️ @lana_surskaya \nи поделиться с подружкой 😉")

    return "\n".join(lines).strip()


# ── Entry point ──────────────────────────────────────────────────────────────


COMMANDS = {
    "generate": cmd_generate,
    "publish": cmd_publish,
    "publish_next": cmd_publish_next,
    "publish_smart": cmd_publish_smart,
    "cleanup_stale": cmd_cleanup_stale,
    "schedule": cmd_schedule,
    "generate_and_schedule": cmd_generate_and_schedule,
    "full": cmd_full,
    "render_test": cmd_render_test,
}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    command = sys.argv[1] if len(sys.argv) > 1 else "full"

    if command not in COMMANDS:
        print(f"Unknown command: '{command}'")
        print(f"Available: {', '.join(COMMANDS)}")
        sys.exit(1)

    print(f"Running: {command}")
    COMMANDS[command]()


if __name__ == "__main__":
    main()
