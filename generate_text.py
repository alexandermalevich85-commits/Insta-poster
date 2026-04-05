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
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
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
        resp = client.models.generate_content(
            model=model or DEFAULT_TEXT_MODELS["gemini"],
            contents=f"{system_prompt}\n\n{user_message}",
        )
        return resp.text
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

    # Fill in CTA if not provided by LLM
    if cta_options and ("cta_keyword" not in data or "cta_text" not in data):
        cta = random.choice(cta_options)
        data.setdefault("cta_keyword", cta["keyword"])
        data.setdefault("cta_text", cta["text"])
        data.setdefault("ps_text", cta.get("ps", ""))
    elif "cta_keyword" not in data:
        data["cta_keyword"] = "КРАСОТА"
        data["cta_text"] = "Напиши «КРАСОТА» в комменты — поделюсь своей системой естественного омоложения."
        data["ps_text"] = "Возможно, мы больше никогда с тобой не увидимся - подписывайся ✏️✔️ @lana_surskaya и поделиться с подружкой 😉"

    return data


def generate_carousel_batch(
    ideas: list[str],
    count: int = 20,
    provider: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    cta_options: list[dict] | None = None,
    delay_between: float = 2.0,
) -> list[dict]:
    """Generate a batch of carousels from ideas.

    Returns list of carousel dicts.
    """
    if cta_options is None:
        cta_options = _load_cta_options()

    carousels = []
    used_cta_indices = []

    for i, idea in enumerate(ideas[:count]):
        log.info("Generating carousel %d/%d: %s", i + 1, count, idea[:50])

        # Rotate CTA options
        if not used_cta_indices or len(used_cta_indices) >= len(cta_options):
            used_cta_indices = []
        available = [j for j in range(len(cta_options)) if j not in used_cta_indices]
        cta_idx = random.choice(available)
        used_cta_indices.append(cta_idx)
        cta = cta_options[cta_idx]

        try:
            carousel = generate_carousel(
                idea=idea,
                provider=provider,
                system_prompt=system_prompt,
                api_key=api_key,
                model=model,
                cta_options=[cta],
            )
            carousel["idea"] = idea
            carousels.append(carousel)
        except Exception as e:
            log.error("Failed to generate carousel for '%s': %s", idea[:50], e)
            continue

        # Delay between API calls to respect rate limits
        if i < len(ideas) - 1:
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
