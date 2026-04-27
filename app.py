"""Streamlit UI for Instagram carousel auto-poster."""

import json
import os
import sys
import logging

import requests
import streamlit as st

# Bridge Streamlit secrets to os.environ before importing config
try:
    for key, val in st.secrets.items():
        if isinstance(val, str):
            os.environ.setdefault(key, val)
except Exception:
    pass

from config import TEXT_PROVIDER, TEXT_MODEL, POSTS_PER_DAY, TIMEZONE
from generate_text import TEXT_MODELS, DEFAULT_TEXT_MODELS
from app_settings import load_settings as _load_app_settings, save_settings as _save_app_settings, SETTINGS_FILE as _APP_SETTINGS_FILE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PENDING_FILE = os.path.join(BASE_DIR, "pending_posts.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
IDEAS_FILE = os.path.join(BASE_DIR, "ideas.json")
PROMPTS_FILE = os.path.join(BASE_DIR, "prompts.json")
PROVIDER_CFG = os.path.join(BASE_DIR, "provider.cfg")
TMP_DIR = os.path.join(BASE_DIR, "tmp")


# ── JSON helpers ─────────────────────────────────────────────────────────────


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else []


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── GitHub sync helpers ──────────────────────────────────────────────────────

from github_sync import push_file as _github_push_file, pull_file as _github_pull_file, GITHUB_REPO, _get_github_token


def sync_from_github():
    """Pull latest pending_posts.json, ideas.json, history.json from GitHub."""
    pulled = []
    for filename, path in [
        ("pending_posts.json", PENDING_FILE),
        ("ideas.json", IDEAS_FILE),
        ("history.json", HISTORY_FILE),
        ("prompts.json", PROMPTS_FILE),
    ]:
        if _github_pull_file(filename, path):
            pulled.append(filename)
    return pulled


def sync_to_github(files: list[tuple[str, str]], message: str) -> list[str]:
    """Push files to GitHub. Returns list of pushed filenames."""
    pushed = []
    for file_path, commit_msg in files:
        if _github_push_file(file_path, commit_msg or message):
            pushed.append(os.path.basename(file_path))
    return pushed


# ── Provider.cfg helpers ─────────────────────────────────────────────────────


def load_provider_cfg() -> dict:
    cfg = {}
    try:
        with open(PROVIDER_CFG, "r") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, val = line.split("=", 1)
                    cfg[key.strip()] = val.strip()
    except FileNotFoundError:
        pass
    return cfg


def save_provider_cfg(cfg: dict):
    with open(PROVIDER_CFG, "w") as f:
        for key, val in cfg.items():
            f.write(f"{key}={val}\n")


# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Insta-poster",
    page_icon="📸",
    layout="wide",
)

st.title("📸 Insta-poster — Автопостер каруселей")

# ── Sidebar: Settings ────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Настройки")

    provider = st.selectbox(
        "Провайдер текста",
        ["gemini", "claude", "openai"],
        index=["gemini", "claude", "openai"].index(TEXT_PROVIDER) if TEXT_PROVIDER in ["gemini", "claude", "openai"] else 0,
    )

    models = TEXT_MODELS.get(provider, [])
    default_model = TEXT_MODEL or DEFAULT_TEXT_MODELS.get(provider, "")
    model_idx = models.index(default_model) if default_model in models else 0
    model = st.selectbox("Модель", models, index=model_idx)

    st.divider()
    st.subheader("API ключи")

    claude_key = st.text_input("Claude API Key", value=os.getenv("CLAUDE_API_KEY", ""), type="password")
    gemini_key = st.text_input("Gemini API Key", value=os.getenv("GEMINI_API_KEY", ""), type="password")
    openai_key = st.text_input("OpenAI API Key", value=os.getenv("OPENAI_API_KEY", ""), type="password")

    st.divider()
    st.subheader("Instagram")
    st.caption("✅ Публикация через Instagram Graph API. Все секреты хранятся в GitHub Actions — отсюда менять ничего не нужно.")

    # Hardcoded: Graph API — единственный рабочий способ
    ig_method = "graph_api"
    ig_username = ""
    ig_password = ""
    imgbb_key = ""
    ig_token = os.getenv("IG_ACCESS_TOKEN", "")
    ig_user_id = os.getenv("IG_USER_ID", "")

    with st.expander("Дополнительно (не обязательно)"):
        ig_token = st.text_input("IG Access Token", value=ig_token, type="password",
                                  help="Используется только если запускаешь приложение локально. На Streamlit Cloud не требуется.")
        ig_user_id = st.text_input("IG User ID", value=ig_user_id,
                                    help="Используется только если запускаешь приложение локально.")

    st.divider()
    st.subheader("🤖 Автоматизация (GitHub Actions)")

    _app_settings = _load_app_settings()

    auto_gen = st.toggle(
        "Автогенерация постов",
        value=_app_settings["auto_generate_enabled"],
        help="Каждое утро GitHub Actions создаёт посты на следующий день. Если выключить — новые посты создаваться не будут.",
    )
    auto_pub = st.toggle(
        "Автопланирование публикаций",
        value=_app_settings["auto_publish_enabled"],
        help="Все посты дня планируются в Instagram через publish_at. Первая публикация выйдет в указанное время слота #1, дальше по слотам. Если выключить — посты в IG не отправляются.",
    )

    posts_per_day = st.number_input(
        "Постов в день",
        min_value=1, max_value=20,
        value=_app_settings["posts_per_day"],
        help="Сколько постов генерировать и публиковать в день. Рекомендовано не более 5/день — IG может счесть спамом.",
    )

    st.markdown("**Время публикаций (МСК)**")
    st.caption("По одной строке HH:MM на каждый пост. 1-й публикуется сразу, остальные — на это время.")

    publish_times: list[str] = []
    cur_times = _app_settings["publish_times"]
    for i in range(int(posts_per_day)):
        default_time = cur_times[i] if i < len(cur_times) else "12:00"
        try:
            hh, mm = map(int, default_time.split(":"))
            from datetime import time as _time
            t_default = _time(hh, mm)
        except Exception:
            from datetime import time as _time
            t_default = _time(12, 0)
        t_picked = st.time_input(
            f"Слот #{i+1}",
            value=t_default,
            key=f"slot_time_{i}",
            step=300,  # 5-minute step
        )
        publish_times.append(t_picked.strftime("%H:%M"))

    if st.button("💾 Сохранить автоматизацию", type="primary"):
        _save_app_settings({
            "auto_generate_enabled": bool(auto_gen),
            "auto_publish_enabled": bool(auto_pub),
            "posts_per_day": int(posts_per_day),
            "publish_times": publish_times,
        })
        # Push to GitHub so workflows pick it up
        try:
            sync_to_github(
                [(_APP_SETTINGS_FILE, "Update automation settings via Streamlit")],
                "Update automation settings via Streamlit",
            )
            st.success("✅ Настройки сохранены и отправлены в GitHub")
        except Exception as e:
            st.warning(f"Сохранено локально, но не удалось запушить: {e}")

    st.divider()

    if st.button("Сохранить настройки"):
        # Save to provider.cfg
        cfg = {
            "TEXT_PROVIDER": provider,
            "TEXT_MODEL": model,
            "IG_PUBLISH_METHOD": ig_method,
            "POSTS_PER_DAY": str(posts_per_day),
        }
        save_provider_cfg(cfg)

        # Update env vars for current session
        os.environ["TEXT_PROVIDER"] = provider
        os.environ["TEXT_MODEL"] = model
        if claude_key: os.environ["CLAUDE_API_KEY"] = claude_key
        if gemini_key: os.environ["GEMINI_API_KEY"] = gemini_key
        if openai_key: os.environ["OPENAI_API_KEY"] = openai_key
        os.environ["IG_PUBLISH_METHOD"] = ig_method
        if ig_method == "instagrapi":
            if ig_username: os.environ["INSTAGRAPI_USERNAME"] = ig_username
            if ig_password: os.environ["INSTAGRAPI_PASSWORD"] = ig_password
        else:
            if ig_token: os.environ["IG_ACCESS_TOKEN"] = ig_token
            if ig_user_id: os.environ["IG_USER_ID"] = ig_user_id
            if imgbb_key: os.environ["IMGBB_API_KEY"] = imgbb_key

        st.success("Настройки сохранены!")

    st.divider()
# ── Tabs ─────────────────────────────────────────────────────────────────────

tab_queue, tab_preview, tab_prompts, tab_history, tab_gen = st.tabs(
    ["Очередь", "Превью", "Промпты", "История", "Генерация"]
)


# ── Tab 1: Generation ────────────────────────────────────────────────────────

with tab_gen:
    st.header("Генерация каруселей")

    gen_count = st.number_input("Количество", min_value=1, max_value=50, value=posts_per_day, key="gen_count")
    gen_provider = provider  # берём из sidebar

    if st.button("Сгенерировать карусели", type="primary"):
        from generate_text import generate_carousel_batch, generate_ideas
        from utils import calculate_schedule_times
        import uuid

        prompts = load_json(PROMPTS_FILE, {})
        system_prompt = prompts.get("system_prompt")
        cta_options = prompts.get("cta_options", [])
        ideas_prompt = prompts.get("ideas_prompt")
        target_audience = prompts.get("target_audience")

        # Load or generate ideas
        ideas = load_json(IDEAS_FILE, [])
        unused = [(i, item["idea"]) for i, item in enumerate(ideas) if not item.get("used")]

        if len(unused) < gen_count:
            with st.spinner("Генерирую идеи..."):
                existing = [item["idea"] for item in ideas]
                new_ideas = generate_ideas(
                    count=gen_count - len(unused),
                    existing_ideas=existing,
                    provider=gen_provider,
                    ideas_prompt=ideas_prompt,
                    target_audience=target_audience,
                )
                for idea_text in new_ideas:
                    ideas.append({"idea": idea_text, "used": False})
                save_json(IDEAS_FILE, ideas)
                unused = [(i, item["idea"]) for i, item in enumerate(ideas) if not item.get("used")]

        idea_texts = [text for _, text in unused[:gen_count]]
        idea_indices = [idx for idx, _ in unused[:gen_count]]

        progress = st.progress(0)
        status = st.empty()

        with st.spinner("Генерирую карусели..."):
            carousels = generate_carousel_batch(
                ideas=idea_texts,
                count=gen_count,
                provider=gen_provider,
                system_prompt=system_prompt,
                cta_options=cta_options,
            )

        if not carousels:
            st.error("Не удалось сгенерировать карусели. Проверьте API-ключ и попробуйте снова.")
            st.stop()

        # Schedule times
        schedule_times = calculate_schedule_times(count=len(carousels))

        # Save
        pending = load_json(PENDING_FILE, [])
        for i, c in enumerate(carousels):
            cid = str(uuid.uuid4())[:8]
            post = {
                "id": cid,
                "status": "pending",
                "created_at": __import__("datetime").datetime.now().isoformat(),
                "scheduled_at": schedule_times[i].isoformat() if i < len(schedule_times) else None,
                "headline": c["headline"],
                "points": c["points"],
                "cta_keyword": c.get("cta_keyword", ""),
                "cta_text": c.get("cta_text", ""),
                "ps_text": c.get("ps_text", ""),
                "caption": "\n\n".join([c["headline"]] + c["points"] + [c.get("cta_text", "")]),
                "text_provider": gen_provider,
                "idea": c.get("idea", ""),
                "published_at": None,
                "ig_media_id": None,
            }
            pending.append(post)
            if i < len(idea_indices):
                ideas[idea_indices[i]]["used"] = True

        save_json(PENDING_FILE, pending)
        save_json(IDEAS_FILE, ideas)

        st.success(f"Сгенерировано {len(carousels)} каруселей!")

        for i, c in enumerate(carousels):
            time_str = schedule_times[i].strftime("%H:%M") if i < len(schedule_times) else "?"
            st.write(f"**{i+1}. [{time_str}]** {c['headline'][:80]}...")


# ── Tab 2: Queue ─────────────────────────────────────────────────────────────

with tab_queue:
    st.header("Очередь публикации")

    # GitHub connection status — must be configured in Streamlit Cloud Secrets
    _gh_token = _get_github_token()

    # Debug info — what does Streamlit actually see?
    with st.expander("🔍 Диагностика подключения GitHub", expanded=not _gh_token):
        _env_token = os.getenv("GITHUB_TOKEN")
        _secrets_token = None
        try:
            _secrets_token = st.secrets.get("GITHUB_TOKEN") if hasattr(st, 'secrets') else None
        except Exception as _e:
            st.write(f"st.secrets ошибка: `{_e}`")

        st.write(f"- В переменной окружения GITHUB_TOKEN: {'✅ ' + _env_token[:8] + '...' if _env_token else '❌ нет'}")
        st.write(f"- В st.secrets[GITHUB_TOKEN]: {'✅ ' + _secrets_token[:8] + '...' if _secrets_token else '❌ нет'}")
        try:
            _all_secret_keys = list(st.secrets.keys()) if hasattr(st, 'secrets') else []
            st.write(f"- Все ключи в st.secrets: `{_all_secret_keys}`")
        except Exception as _e:
            st.write(f"- Список секретов недоступен: `{_e}`")

        if _gh_token:
            # Live check: try to actually fetch a file
            import requests as _r
            try:
                _r2 = _r.get(
                    f"https://api.github.com/repos/{GITHUB_REPO}/contents/pending_posts.json",
                    headers={"Authorization": f"token {_gh_token}"},
                    timeout=10,
                )
                st.write(f"- Тест запроса: HTTP **{_r2.status_code}**")
                if _r2.status_code != 200:
                    st.code(_r2.text[:400])
            except Exception as _e:
                st.write(f"- Тест запроса упал: `{_e}`")

    if not _gh_token:
        st.error(
            "❌ **GITHUB_TOKEN не настроен.** Без него Streamlit не может загружать посты из репозитория.\n\n"
            "**Как добавить:**\n"
            "1. Открой Streamlit Cloud → Manage app → Settings → Secrets\n"
            "2. Добавь строку: `GITHUB_TOKEN = \"github_pat_...\"` (с кавычками)\n"
            "3. Save → дождись перезапуска (30 сек)"
        )
    else:
        st.caption(f"✅ GitHub подключён ({_gh_token[:8]}...)")

    pending = load_json(PENDING_FILE, [])

    # ── Today's & tomorrow's plan summary ─────────────────────────────────────
    from datetime import datetime as _dt, date as _d, timedelta as _td
    import pytz as _pytz
    _tz = _pytz.timezone(TIMEZONE)
    _today = _dt.now(_tz).date()
    _tomorrow = _today + _td(days=1)

    _by_day: dict = {}  # date -> list of (post, sched_dt)
    for _p in pending:
        _sched = _p.get("scheduled_at")
        if not _sched:
            continue
        try:
            _sd = _dt.fromisoformat(_sched)
            if _sd.tzinfo is None:
                _sd = _tz.localize(_sd)
            _by_day.setdefault(_sd.date(), []).append((_p, _sd))
        except Exception:
            pass
    for _d_key in _by_day:
        _by_day[_d_key].sort(key=lambda x: x[1])

    _icon_map = {"published": "✅", "scheduled": "🕐", "pending": "🟡", "failed": "❌", "schedule_failed": "❌", "expired": "⏭️"}

    def _render_day_block(label: str, the_date, posts):
        if not posts:
            return
        published = sum(1 for p, _ in posts if p.get("status") == "published")
        scheduled = sum(1 for p, _ in posts if p.get("status") == "scheduled")
        pending_cnt = sum(1 for p, _ in posts if p.get("status") == "pending")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"{label} всего", len(posts))
        c2.metric("Опубликовано", published)
        c3.metric("Запланировано в IG", scheduled)
        c4.metric("В ожидании", pending_cnt)
        with st.expander(f"📅 {label} ({the_date.strftime('%d.%m.%Y')}) — план"):
            for p, sd in posts:
                status = p.get("status", "")
                ico = _icon_map.get(status, "⚪")
                st.write(f"{ico} **{sd.strftime('%H:%M')}** — {p.get('headline','')[:80]} _({status})_")

    _today_posts = _by_day.get(_today, [])
    _tomorrow_posts = _by_day.get(_tomorrow, [])

    if _today_posts or _tomorrow_posts:
        st.subheader("Сегодня")
        _render_day_block("Сегодня", _today, _today_posts) if _today_posts else st.caption("Постов на сегодня нет.")

        st.subheader("Завтра")
        _render_day_block("Завтра", _tomorrow, _tomorrow_posts) if _tomorrow_posts else st.caption("Постов на завтра ещё нет — будут созданы автогенерацией в 05:00 МСК.")

        # Future days (later than tomorrow)
        _later_dates = sorted(d for d in _by_day if d > _tomorrow)
        if _later_dates:
            with st.expander(f"📆 Дальше ({len(_later_dates)} дн.)"):
                for d in _later_dates:
                    posts = _by_day[d]
                    st.markdown(f"**{d.strftime('%d.%m.%Y')}** — {len(posts)} пост(ов)")
                    for p, sd in posts:
                        status = p.get("status", "")
                        ico = _icon_map.get(status, "⚪")
                        st.write(f"  {ico} {sd.strftime('%H:%M')} — {p.get('headline','')[:80]} _({status})_")
        st.divider()
    # ─────────────────────────────────────────────────────────────────────────

    # Sync button always visible
    if st.button("🔄 Загрузить посты из GitHub"):
        token = _get_github_token()
        if not token:
            st.error("GITHUB_TOKEN не найден в секретах! Добавьте его в Settings → Secrets.")
        else:
            st.info(f"Токен найден ({token[:10]}...), загружаю...")
            try:
                pulled = sync_from_github()
                if pulled:
                    # Check how many posts loaded
                    new_pending = load_json(PENDING_FILE, [])
                    st.success(f"Загружено: {', '.join(pulled)}. Постов в очереди: {len(new_pending)}")
                    st.rerun()
                else:
                    # Debug: try manual request
                    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/pending_posts.json"
                    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
                    resp = requests.get(api_url, headers=headers)
                    st.error(f"Ошибка загрузки. API статус: {resp.status_code}. Ответ: {resp.text[:300]}")
            except Exception as e:
                import traceback
                st.error(f"Ошибка: {e}")
                st.code(traceback.format_exc())

    if not pending:
        st.info("Очередь пуста. Нажмите кнопку выше чтобы загрузить посты из GitHub.")
    else:
        status_colors = {
            "pending": "🟡",
            "scheduled": "🔵",
            "published": "🟢",
            "failed": "🔴",
            "schedule_failed": "🔴",
        }

        for i, post in enumerate(pending):
            status_icon = status_colors.get(post.get("status", ""), "⚪")
            scheduled = post.get("scheduled_at", "")
            time_str = scheduled[11:16] if scheduled and len(scheduled) > 16 else "—"

            col1, col2, col3, col4, col5 = st.columns([0.5, 1, 5, 1.2, 1.2])
            with col1:
                st.write(status_icon)
            with col2:
                st.write(f"**{time_str}**")
            with col3:
                st.write(post.get("headline", "")[:80])
            with col4:
                _is_local = os.path.exists(os.path.join(os.path.dirname(__file__), "ig_session_lana_surskaya.json"))
                if _is_local and post.get("status") in ("pending", "failed", "retry", "schedule_failed"):
                    if st.button("Отправить", key=f"pub_{i}"):
                        with st.spinner("Публикация..."):
                            try:
                                from render_slides import render_carousel as _render
                                from post_instagram import publish_carousel as _publish
                                from main import add_history_entry, _build_caption
                                import os as _os
                                from utils import ensure_dir
                                _out = ensure_dir(_os.path.join("tmp", post["id"]))
                                _imgs = _render(post, _out)
                                _caption = post.get("caption") or _build_caption(post)
                                _res = _publish(_imgs, _caption, method=_os.getenv("IG_PUBLISH_METHOD"))
                                _mid = _res.get("media_id")
                                add_history_entry(post, _mid)
                                # Remove from pending so autopublish won't re-publish
                                pending.pop(i)
                                save_json(PENDING_FILE, pending)
                                sync_to_github(
                                    [(PENDING_FILE, "Manual publish via Streamlit")],
                                    "Manual publish via Streamlit",
                                )
                                st.success(f"Опубликовано!")
                                st.rerun()
                            except Exception as _e:
                                pending[i]["status"] = "failed"
                                pending[i]["error"] = str(_e)
                                save_json(PENDING_FILE, pending)
                                st.error(f"Ошибка: {_e}")
            with col5:
                if post.get("status") in ("failed", "retry", "schedule_failed"):
                    if st.button("Повторить", key=f"retry_{i}"):
                        pending[i]["status"] = "pending"
                        pending[i]["error"] = None
                        save_json(PENDING_FILE, pending)
                        st.rerun()
                if post.get("status") in ("pending", "failed", "retry", "schedule_failed"):
                    if st.button("Удалить", key=f"del_{i}"):
                        pending.pop(i)
                        save_json(PENDING_FILE, pending)
                        sync_to_github(
                            [(PENDING_FILE, "Delete post via Streamlit")],
                            "Delete post via Streamlit",
                        )
                        st.rerun()

        st.divider()
        if st.button("Очистить опубликованные"):
            pending = [p for p in pending if p.get("status") not in ("published", "scheduled", "failed")]
            save_json(PENDING_FILE, pending)
            sync_to_github(
                [(PENDING_FILE, "Clear published posts via Streamlit")],
                "Clear published posts via Streamlit",
            )
            st.success("Очищено!")
            st.rerun()


# ── Tab 3: Preview ───────────────────────────────────────────────────────────

with tab_preview:
    st.header("Превью карусели")

    all_posts = load_json(PENDING_FILE, [])

    if not all_posts:
        st.info("Нет карусельных постов для превью.")
    else:
        status_icons = {"pending": "🟡", "published": "🟢", "failed": "🔴", "scheduled": "🔵"}
        options = [f"{status_icons.get(p.get('status',''), '⚪')} {p['id']} — {p['headline'][:50]}..." for p in all_posts]
        selected_idx = st.selectbox("Выберите карусель", range(len(options)), format_func=lambda x: options[x])

        selected = all_posts[selected_idx]

        # Render slides
        if st.button("Отрендерить превью", type="primary"):
            from render_slides import render_carousel
            from utils import ensure_dir

            output_dir = ensure_dir(os.path.join(TMP_DIR, f"preview_{selected['id']}"))
            with st.spinner("Рендеринг слайдов..."):
                paths = render_carousel(selected, output_dir)

            st.session_state[f"preview_paths_{selected['id']}"] = paths

        # Show rendered slides
        preview_key = f"preview_paths_{selected['id']}"
        if preview_key in st.session_state:
            paths = st.session_state[preview_key]
            cols = st.columns(4)
            for i, path in enumerate(paths):
                with cols[i % 4]:
                    st.image(path, caption=f"Слайд {i+1}", use_container_width=True)

        # Edit fields
        st.divider()
        st.subheader("Редактирование")

        new_headline = st.text_area("Заголовок", value=selected.get("headline", ""), key=f"edit_headline_{selected['id']}")

        for j in range(5):
            point_val = selected["points"][j] if j < len(selected.get("points", [])) else ""
            selected["points"][j] = st.text_area(
                f"Пункт {j+1}", value=point_val, key=f"edit_point_{selected['id']}_{j}"
            )

        new_cta = st.text_area("CTA", value=selected.get("cta_text", ""), key=f"edit_cta_{selected['id']}")
        new_ps = st.text_area("P.S.", value=selected.get("ps_text", ""), key=f"edit_ps_{selected['id']}")

        if st.button("Сохранить изменения"):
            # Find and update in pending
            for p in pending:
                if p["id"] == selected["id"]:
                    p["headline"] = new_headline
                    p["cta_text"] = new_cta
                    p["ps_text"] = new_ps
                    p["caption"] = "\n\n".join([new_headline] + p["points"] + [new_cta])
                    break
            save_json(PENDING_FILE, pending)
            pushed = sync_to_github(
                [(PENDING_FILE, "Update post via Streamlit editor")],
                "Update post via Streamlit editor",
            )
            if pushed:
                st.success("Сохранено и отправлено в GitHub!")
            else:
                st.success("Сохранено локально.")
                st.warning("Не удалось отправить в GitHub. Проверьте GITHUB_TOKEN.")


# ── Tab 4: History ───────────────────────────────────────────────────────────

with tab_history:
    st.header("История публикаций")

    history = load_json(HISTORY_FILE, [])

    if not history:
        st.info("Пока нет опубликованных каруселей.")
    else:
        st.metric("Всего опубликовано", len(history))

        for idx, entry in enumerate(reversed(history[-50:])):
            with st.expander(f"📅 {entry.get('date', '')[:10]} — {entry.get('headline', '')[:60]}"):
                st.write(f"**Тема:** {entry.get('topic', '—')}")
                st.write(f"**Провайдер:** {entry.get('text_provider', '—')}")
                st.write(f"**IG Media ID:** {entry.get('ig_media_id', '—')}")
                if entry.get("caption"):
                    st.text_area("Caption", value=entry["caption"], disabled=True, key=f"hist_{idx}_{entry.get('id', '')}")


# ── Tab 5: Prompts ───────────────────────────────────────────────────────────

with tab_prompts:
    st.header("Промпты")

    prompts = load_json(PROMPTS_FILE, {})

    new_system = st.text_area(
        "Системный промпт",
        value=prompts.get("system_prompt", ""),
        height=400,
        key="edit_system_prompt",
    )

    new_ideas_prompt = st.text_area(
        "Промпт для генерации идей",
        value=prompts.get("ideas_prompt", ""),
        height=100,
        key="edit_ideas_prompt",
    )

    new_audience = st.text_area(
        "Целевая аудитория",
        value=prompts.get("target_audience", ""),
        height=80,
        key="edit_audience",
    )

    st.subheader("CTA варианты")
    cta_options = prompts.get("cta_options", [])
    for i, cta in enumerate(cta_options):
        col1, col2 = st.columns(2)
        with col1:
            cta["keyword"] = st.text_input(f"Ключевое слово {i+1}", value=cta.get("keyword", ""), key=f"cta_kw_{i}")
        with col2:
            cta["text"] = st.text_input(f"CTA текст {i+1}", value=cta.get("text", ""), key=f"cta_text_{i}")

    if st.button("Сохранить промпты"):
        prompts["system_prompt"] = new_system
        prompts["ideas_prompt"] = new_ideas_prompt
        prompts["target_audience"] = new_audience
        prompts["cta_options"] = cta_options
        save_json(PROMPTS_FILE, prompts)

        # Auto-push to GitHub
        pushed = sync_to_github(
            [(PROMPTS_FILE, "Update prompts from Streamlit UI")],
            "Update prompts",
        )
        if pushed:
            st.success("Промпты сохранены и отправлены в GitHub!")
        else:
            st.success("Промпты сохранены локально.")
            st.warning("Для синхронизации с GitHub добавьте GITHUB_TOKEN в секреты.")
