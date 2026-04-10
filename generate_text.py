"""LLM-based carousel text generation for Instagram posts."""

from __future__ import annotations

import json
import logging
import random
import re
import time

from config import TEXT_PROVIDER, CLAUDE_API_KEY, OPENAI_API_KEY, get_gemini_client

log = logging.getLogger("insta-poster")

# ── Available models per provider ────────────────────────────────────────────

TEXT_MODELS = {
    "claude": [
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-sonnet-4-5-20250929",
        "claude-opus-4-20250514",
        "claude-haiku-3-5-20241022",
    ],
    "gemini": [
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
    ],
    "openai": [
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-4o",
        "gpt-4o-mini",
        "o3-mini",
    ],
}

DEFAULT_TEXT_MODELS = {
    "claude": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4.1",
}

DEFAULT_IDEAS_PROMPT = """\
Сгенерируй {count} уникальных тем для Instagram-каруселей.
Темы рандомно из категорий: омоложение лица, здоровье, психология, онлайн бизнес для женщин.
Каждая тема должна быть провокационной, цепляющей, вызывать желание пролистать карусель.
Формат: одна тема на строку, без нумерации.\
"""


# ── LLM providers ────────────────────────────────────────────────────────────


def _call_llm_raw(
    provider: str,
    system_prompt: str,
    user_message: str,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """Call an LLM provider and return raw text response."""
    prov = provider or TEXT_PROVIDER
    if prov == "claude":
        import anthropic
        key = api_key or CLAUDE_API_KEY
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=model or DEFAULT_TEXT_MODELS["claude"],
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return msg.content[0].text
    elif prov == "gemini":
        client = get_gemini_client(api_key_override=api_key)
        mdl = model or DEFAULT_TEXT_MODELS["gemini"]
        fallback_models = [mdl, "gemini-2.0-flash", "gemini-2.0-flash-lite"]
        # Remove duplicates while preserving order
        seen = set()
        fallback_models = [m for m in fallback_models if m not in seen and not seen.add(m)]

        for model_name in fallback_models:
            # Primary model gets 15 attempts (up to ~10 min), fallbacks get 5
            max_attempts = 15 if model_name == mdl else 5
            for attempt in range(max_attempts):
                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=f"{system_prompt}\n\n{user_message}",
                    )
                    if model_name != mdl:
                        log.info("Using fallback model: %s", model_name)
                    return resp.text
                except Exception as e:
                    err = str(e)
                    if "429" in err or "RESOURCE_EXHAUSTED" in err or "503" in err or "UNAVAILABLE" in err:
                        wait = 30 * (attempt + 1) if model_name == mdl else 15 * (attempt + 1)
                        log.warning("Gemini %s error (%s), waiting %ds (attempt %d/%d)...",
                                    model_name, err[:80], wait, attempt + 1, max_attempts)
                        time.sleep(wait)
                    else:
                        raise
            log.warning("Model %s failed after %d retries, trying next fallback...", model_name, max_attempts)
        raise RuntimeError("All Gemini models failed after retries")
    elif prov == "openai":
        import openai
        key = api_key or OPENAI_API_KEY
        client = openai.OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model=model or DEFAULT_TEXT_MODELS["openai"],
            max_tokens=2000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return resp.choices[0].message.content
    else:
        raise ValueError(f"Unknown provider: '{prov}'")


# ── Carousel generation ──────────────────────────────────────────────────────


def _parse_carousel_json(raw: str) -> dict:
    """Parse carousel JSON from LLM response, stripping markdown fences."""
    text = raw.strip()
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    data = json.loads(text)

    # Validate required fields
    required = ["headline", "points"]
    for key in required:
        if key not in data:
            raise ValueError(f"Missing required field: {key}")

    if not isinstance(data["points"], list) or len(data["points"]) != 5:
        raise ValueError(f"Expected 5 points, got {len(data.get('points', []))}")

    return data


def generate_carousel(
    idea: str,
    provider: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    cta_options: list[dict] | None = None,
) -> dict:
    """Generate a single carousel text from an idea.

    Returns dict with: headline, points, cta_keyword, cta_text, ps_text.
    """
    from utils import retry

    prov = provider or TEXT_PROVIDER

    # Validate model
    if model and prov in TEXT_MODELS and model not in TEXT_MODELS[prov]:
        raise ValueError(
            f"Unknown model '{model}' for provider '{prov}'. "
            f"Available: {', '.join(TEXT_MODELS[prov])}"
        )

    # Load default system prompt from prompts.json if not provided
    if system_prompt is None:
        system_prompt = _load_default_system_prompt()

    user_msg = f"Создай пост-карусель на тему: {idea}"

    def _generate():
        raw = _call_llm_raw(prov, system_prompt, user_msg, api_key, model)
        return _parse_carousel_json(raw)

    data = retry(_generate, max_attempts=3, delay=2.0)

    # Always override CTA with one from cta_options (don't use LLM-generated keywords)
    if cta_options:
        cta = random.choice(cta_options)
        data["cta_keyword"] = cta["keyword"]
        data["cta_text"] = cta["text"]
        data["ps_text"] = cta.get("ps", "")
    else:
        data.setdefault("cta_keyword", "КРАСОТА")
        data.setdefault("cta_text", "Напиши «КРАСОТА» в комменты — поделюсь своей системой естественного омоложения.")
        data.setdefault("ps_text", "Возможно, мы больше никогда с тобой не увидимся - подпи sывайся ✏️✔️ @lana_surskaya и поделиться с подружкой 😉")

    return data


def _generate_batch_prompt(ideas_chunk: list[str], cta_options: list[dict]) -> str:
    """Build a prompt to generate multiple carousels in one API call."""
    cta_examples = []
    for i, idea in enumerate(ideas_chunk):
        cta = cta_options[i % len(cta_options)]
        cta_examples.append(f'  Карусель {i+1}: тема: "{idea}", cta_keyword: "{cta["keyword"]}", cta_text: "{cta["text"]}", ps_text: "{cta.get("ps", "")}"')

    tasks = "\n".join(cta_examples)
    return (
        f"Создай {len(ideas_chunk)} каруселей СРАЗУ. Для каждой используй указанную тему и CTA.\n\n"
        f"{tasks}\n\n"
        f"Формат ответа — ТОЛЬКО валидный JSON-массив (без markdown-блоков ```json):\n"
        f"[\n"
        f'  {{"headline": "...", "points": ["1. ...", "2. ...", "3. ...", "4. ...", "5. ..."], '
        f'"cta_keyword": "...", "cta_text": "...", "ps_text": "..."}},\n'
        f"  ...\n"
        f"]\n"
    )


def _parse_batch_json(raw: str) -> list[dict]:
    """Parse batch carousel JSON from LLM response."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    data = json.loads(text)
    if not isinstance(data, list):
        data = [data]

    results = []
    for item in data:
        if "headline" in item and "points" in item:
            if isinstance(item["points"], list) and len(item["points"]) == 5:
                results.append(item)
            else:
                log.warning("Skipping carousel with %d points", len(item.get("points", [])))
        else:
            log.warning("Skipping carousel without headline/points")
    return results


def generate_carousel_batch(
    ideas: list[str],
    count: int = 20,
    provider: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    cta_options: list[dict] | None = None,
    delay_between: float = 5.0,
    batch_size: int = 2,
) -> list[dict]:
    """Generate a batch of carousels from ideas.

    Generates multiple carousels per API call to minimize API usage.
    Returns list of carousel dicts.
    """
    from utils import retry

    if cta_options is None:
        cta_options = _load_cta_options()

    if system_prompt is None:
        system_prompt = _load_default_system_prompt()

    prov = provider or TEXT_PROVIDER
    ideas_list = ideas[:count]
    carousels = []

    # Split into batches
    for batch_start in range(0, len(ideas_list), batch_size):
        chunk = ideas_list[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (len(ideas_list) + batch_size - 1) // batch_size
        log.info("Generating batch %d/%d (%d carousels)...", batch_num, total_batches, len(chunk))

        user_msg = _generate_batch_prompt(chunk, cta_options)

        def _generate():
            raw = _call_llm_raw(prov, system_prompt, user_msg, api_key, model)
            return _parse_batch_json(raw)

        try:
            batch_results = retry(_generate, max_attempts=3, delay=5.0)
            for i, carousel in enumerate(batch_results):
                if batch_start + i < len(ideas_list):
                    carousel["idea"] = ideas_list[batch_start + i]
                carousels.append(carousel)
            log.info("Batch %d: got %d carousels", batch_num, len(batch_results))
        except Exception as e:
            log.error("Batch %d failed: %s", batch_num, e)
            # Fallback: generate one by one
            for i, idea in enumerate(chunk):
                try:
                    carousel = generate_carousel(
                        idea=idea, provider=provider,
                        system_prompt=system_prompt, api_key=api_key,
                        model=model, cta_options=cta_options,
                    )
                    carousel["idea"] = idea
                    carousels.append(carousel)
                except Exception as e2:
                    log.error("Failed carousel '%s': %s", idea[:50], e2)
                if i < len(chunk) - 1:
                    time.sleep(delay_between)

        # Delay between batches
        if batch_start + batch_size < len(ideas_list):
            time.sleep(delay_between)

    return carousels


# ── Idea generation ──────────────────────────────────────────────────────────


def _parse_ideas(raw_text: str) -> list[str]:
    """Parse LLM response into a list of idea strings."""
    ideas: list[str] = []
    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^\d+[\.\)]\s*", "", line)
        line = re.sub(r"^[-•\*]\s*", "", line)
        line = line.strip().strip('"').strip("«»").strip()
        if line:
            ideas.append(line)
    return ideas


def generate_ideas(
    count: int = 10,
    existing_ideas: list[str] | None = None,
    provider: str | None = None,
    ideas_prompt: str | None = None,
    target_audience: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> list[str]:
    """Generate new carousel topic ideas using an LLM."""
    sys = system_prompt or "Ты — креативный копирайтер для Instagram."
    if target_audience:
        sys += f"\n\nЦелевая аудитория: {target_audience}"

    prompt_tpl = ideas_prompt or DEFAULT_IDEAS_PROMPT
    try:
        user_msg = prompt_tpl.format(count=count)
    except KeyError:
        user_msg = prompt_tpl

    if existing_ideas:
        existing_block = "\n".join(f"- {idea}" for idea in existing_ideas)
        user_msg += f"\n\nУже существующие идеи (НЕ повторяй):\n{existing_block}"

    raw = _call_llm_raw(provider, sys, user_msg, api_key, model=model)
    return _parse_ideas(raw)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_default_system_prompt() -> str:
    """Load system prompt from prompts.json."""
    import os
    prompts_path = os.path.join(os.path.dirname(__file__), "prompts.json")
    try:
        with open(prompts_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("system_prompt", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def _load_cta_options() -> list[dict]:
    """Load CTA options from prompts.json."""
    import os
    prompts_path = os.path.join(os.path.dirname(__file__), "prompts.json")
    try:
        with open(prompts_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("cta_options", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []
