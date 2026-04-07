"""Render Instagram carousel slides as 1080x1350 images using Pillow.

Supports emoji rendering via Noto Color Emoji (bitmap font, fixed 109px)
with automatic scaling to match text height.
"""

from __future__ import annotations

import os
import re
import textwrap

from PIL import Image, ImageDraw, ImageFont

# ── Constants ────────────────────────────────────────────────────────────────

SLIDE_WIDTH = 1080
SLIDE_HEIGHT = 1350
BG_COLOR = "#F5F0EB"
USERNAME_COLOR = "#3B7DD8"
TEXT_COLOR = "#1A1A1A"
FOOTER_COLOR = "#888888"
SEPARATOR_GRAY = "#CCCCCC"

USERNAME_TEXT = "@LANA_SURSKAYA"
FOOTER_LEFT = "ПОДЕЛИТЬСЯ"
FOOTER_RIGHT = "СОХРАНИТЬ"

MARGIN_X = 80
MARGIN_TOP = 55
GRADIENT_LINE_Y = 115
GRADIENT_LINE_HEIGHT = 3
TEXT_AREA_TOP = 180
TEXT_AREA_BOTTOM = 1220
FOOTER_LINE_Y = 1255
FOOTER_TEXT_Y = 1280

# Gradient colors: coral → gold → sky blue → sage green
GRADIENT_COLORS = [
    (232, 131, 107),
    (212, 169, 106),
    (107, 181, 232),
    (139, 200, 139),
]

# Font sizes
USERNAME_FONT_SIZE = 30
COVER_FONT_SIZE = 54
CONTENT_FONT_SIZE = 44
CTA_FONT_SIZE = 44
PS_FONT_SIZE = 40
FOOTER_FONT_SIZE = 18

# ── Emoji detection ─────────────────────────────────────────────────────────

# Regex matching emoji characters (including variation selectors and ZWJ sequences)
_EMOJI_RE = re.compile(
    "["
    "\U0000200D"          # Zero-width joiner
    "\U0000FE0F"          # Variation selector-16
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Misc symbols & pictographs
    "\U0001F680-\U0001F6FF"  # Transport & map
    "\U0001F900-\U0001F9FF"  # Supplemental symbols
    "\U0001FA00-\U0001FA6F"  # Chess symbols
    "\U0001FA70-\U0001FAFF"  # Symbols extended-A
    "\U00002600-\U000026FF"  # Misc symbols
    "\U00002700-\U000027BF"  # Dingbats
    "\U0000203C-\U0000203C"  # ‼
    "\U00002049-\U00002049"  # ⁉
    "\U0000231A-\U0000231B"  # ⌚⌛
    "\U000023E9-\U000023F3"
    "\U000023F8-\U000023FA"
    "\U000025AA-\U000025AB"
    "\U000025B6\U000025C0"
    "\U000025FB-\U000025FE"
    "\U00002614-\U00002615"
    "\U00002648-\U00002653"
    "\U0000267F\U00002693"
    "\U000026A1\U000026AA-\U000026AB"
    "\U000026BD-\U000026BE"
    "\U000026C4-\U000026C5"
    "\U000026CE-\U000026CF"
    "\U000026D4\U000026EA"
    "\U000026F2-\U000026F3"
    "\U000026F5\U000026FA"
    "\U000026FD"
    "\U00002702\U00002705"
    "\U00002708-\U0000270D"
    "\U0000270F"
    "\U00002712\U00002714"
    "\U00002716\U0000271D"
    "\U00002721\U00002728"
    "\U00002733-\U00002734"
    "\U00002744\U00002747"
    "\U0000274C\U0000274E"
    "\U00002753-\U00002755"
    "\U00002757\U00002763-\U00002764"
    "\U00002795-\U00002797"
    "\U000027A1\U000027B0"
    "\U0000E000-\U0000F8FF"  # Private use area
    "\U0001F000-\U0001F02F"  # Mahjong tiles
    "\U0001F0A0-\U0001F0FF"  # Playing cards
    "]+",
    flags=re.UNICODE,
)


def _has_emoji(text: str) -> bool:
    """Check if text contains emoji characters."""
    return bool(_EMOJI_RE.search(text))


def _split_emoji_segments(text: str) -> list[tuple[str, bool]]:
    """Split text into segments of (text, is_emoji).

    Returns list of tuples where each tuple is (segment_text, is_emoji_segment).
    """
    segments = []
    last_end = 0
    for m in _EMOJI_RE.finditer(text):
        if m.start() > last_end:
            segments.append((text[last_end:m.start()], False))
        segments.append((m.group(), True))
        last_end = m.end()
    if last_end < len(text):
        segments.append((text[last_end:], False))
    return segments


# ── Font paths ───────────────────────────────────────────────────────────────

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_FONTS_DIR = os.path.join(_BASE_DIR, "fonts")

_FONT_PATHS = {
    "evolventa_bold": os.path.join(_FONTS_DIR, "Evolventa-Bold.ttf"),
    "evolventa_regular": os.path.join(_FONTS_DIR, "Evolventa-Regular.ttf"),
    "montserrat_bold": os.path.join(_FONTS_DIR, "Montserrat-Bold.ttf"),
    "montserrat_regular": os.path.join(_FONTS_DIR, "Montserrat-Regular.ttf"),
    "noto_emoji": os.path.join(_FONTS_DIR, "NotoColorEmoji.ttf"),
}

# Fallback to system fonts on macOS
_SYSTEM_FONTS = {
    "montserrat_bold": os.path.expanduser("~/Library/Fonts/MONTSERRAT-BOLD.TTF"),
    "montserrat_regular": os.path.expanduser("~/Library/Fonts/MONTSERRAT-REGULAR.TTF"),
    "noto_emoji": "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",  # Ubuntu/Debian
}

_font_cache: dict[str, ImageFont.FreeTypeFont] = {}
_emoji_font: ImageFont.FreeTypeFont | None = None


def _get_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a font by name and size, with caching."""
    cache_key = f"{name}_{size}"
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    path = _FONT_PATHS.get(name)
    if not path or not os.path.exists(path):
        path = _SYSTEM_FONTS.get(name)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(
            f"Font '{name}' not found. Expected at {_FONT_PATHS.get(name)}"
        )

    font = ImageFont.truetype(path, size)
    _font_cache[cache_key] = font
    return font


def _get_emoji_font() -> ImageFont.FreeTypeFont | None:
    """Load Noto Color Emoji font (fixed 109px bitmap). Cached."""
    global _emoji_font
    if _emoji_font is not None:
        return _emoji_font

    for path in [_FONT_PATHS.get("noto_emoji"), _SYSTEM_FONTS.get("noto_emoji")]:
        if path and os.path.exists(path):
            try:
                _emoji_font = ImageFont.truetype(path, 109)
                return _emoji_font
            except OSError:
                continue
    return None


# ── Drawing helpers ──────────────────────────────────────────────────────────


def _draw_gradient_line(draw: ImageDraw.ImageDraw, y: int, x_start: int, x_end: int, height: int = 3):
    """Draw a horizontal gradient line with 4 color stops."""
    width = x_end - x_start
    colors = GRADIENT_COLORS
    for x in range(width):
        t = x / max(width - 1, 1)
        segment = min(int(t * (len(colors) - 1)), len(colors) - 2)
        local_t = (t * (len(colors) - 1)) - segment
        r = int(colors[segment][0] + (colors[segment + 1][0] - colors[segment][0]) * local_t)
        g = int(colors[segment][1] + (colors[segment + 1][1] - colors[segment][1]) * local_t)
        b = int(colors[segment][2] + (colors[segment + 1][2] - colors[segment][2]) * local_t)
        for dy in range(height):
            draw.point((x_start + x, y + dy), fill=(r, g, b))


def _draw_base_template(draw: ImageDraw.ImageDraw):
    """Draw shared elements: username, gradient line, footer separator, footer text."""
    # Username
    username_font = _get_font("montserrat_bold", USERNAME_FONT_SIZE)
    draw.text((MARGIN_X, MARGIN_TOP), USERNAME_TEXT, font=username_font, fill=USERNAME_COLOR)

    # Gradient line
    _draw_gradient_line(draw, GRADIENT_LINE_Y, MARGIN_X, SLIDE_WIDTH - MARGIN_X, GRADIENT_LINE_HEIGHT)

    # Footer separator line
    draw.line(
        [(MARGIN_X, FOOTER_LINE_Y), (SLIDE_WIDTH - MARGIN_X, FOOTER_LINE_Y)],
        fill=SEPARATOR_GRAY, width=1,
    )

    # Footer text
    footer_font = _get_font("montserrat_bold", FOOTER_FONT_SIZE)

    # Share icon (triangle) + text on left
    share_x = MARGIN_X + 30
    _draw_share_icon(draw, MARGIN_X, FOOTER_TEXT_Y + 2, FOOTER_COLOR)
    draw.text((share_x, FOOTER_TEXT_Y), FOOTER_LEFT, font=footer_font, fill=FOOTER_COLOR)

    # Save icon (bookmark) + text on right
    save_text_width = footer_font.getlength(FOOTER_RIGHT)
    save_x = SLIDE_WIDTH - MARGIN_X - save_text_width
    _draw_bookmark_icon(draw, int(save_x - 28), FOOTER_TEXT_Y + 2, FOOTER_COLOR)
    draw.text((int(save_x), FOOTER_TEXT_Y), FOOTER_RIGHT, font=footer_font, fill=FOOTER_COLOR)


def _draw_share_icon(draw: ImageDraw.ImageDraw, x: int, y: int, color: str):
    """Draw a simple share/send triangle icon."""
    points = [(x, y + 16), (x + 20, y + 8), (x, y)]
    draw.polygon(points, outline=color)
    draw.line([(x + 6, y + 12), (x + 6, y + 20)], fill=color, width=1)


def _draw_bookmark_icon(draw: ImageDraw.ImageDraw, x: int, y: int, color: str):
    """Draw a simple bookmark icon."""
    draw.rectangle([(x, y), (x + 16, y + 20)], outline=color)
    draw.line([(x, y + 20), (x + 8, y + 14)], fill=color, width=1)
    draw.line([(x + 8, y + 14), (x + 16, y + 20)], fill=color, width=1)


def _measure_line_width(line: str, font: ImageFont.FreeTypeFont, target_h: int) -> float:
    """Measure pixel width of a line, accounting for emoji characters.

    Emoji are rendered with Noto Color Emoji at 109px then scaled to target_h,
    so their width is proportionally adjusted.
    """
    if not _has_emoji(line):
        return font.getlength(line)

    emoji_font = _get_emoji_font()
    if not emoji_font:
        return font.getlength(line)

    total = 0.0
    for segment, is_emoji in _split_emoji_segments(line):
        if is_emoji and emoji_font:
            ebbox = emoji_font.getbbox(segment)
            if ebbox:
                ew = ebbox[2] - ebbox[0]
                eh = ebbox[3] - ebbox[1]
                scale = target_h / max(eh, 1)
                total += ew * scale + 4  # 4px gap after emoji
            else:
                total += font.getlength(segment)
        else:
            total += font.getlength(segment)
    return total


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, target_h: int = 40) -> list[str]:
    """Wrap text to fit within max_width pixels."""
    # Estimate chars per line using average character width
    avg_width = font.getlength("А")  # Cyrillic char width
    if avg_width <= 0:
        avg_width = font.getlength("A")
    chars_per_line = max(10, int(max_width / avg_width))

    lines = textwrap.wrap(text, width=chars_per_line)

    # Fine-tune: if any line is too wide, reduce chars and re-wrap
    for _ in range(5):
        too_wide = False
        for line in lines:
            if _measure_line_width(line, font, target_h) > max_width:
                too_wide = True
                break
        if not too_wide:
            break
        chars_per_line = max(5, chars_per_line - 2)
        lines = textwrap.wrap(text, width=chars_per_line)

    return lines


def _render_emoji_image(emoji_text: str, target_h: int) -> Image.Image | None:
    """Render emoji text to a scaled RGBA image matching target_h pixels."""
    emoji_font = _get_emoji_font()
    if not emoji_font:
        return None

    # Render at native 109px on transparent background
    canvas = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    d.text((0, 0), emoji_text, font=emoji_font, embedded_color=True)

    # Crop to content
    bbox = canvas.getbbox()
    if not bbox:
        return None
    cropped = canvas.crop(bbox)

    # Scale to target height
    scale = target_h / max(cropped.height, 1)
    new_w = max(int(cropped.width * scale), 1)
    return cropped.resize((new_w, target_h), Image.LANCZOS)


def _draw_line_with_emoji(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    line: str,
    font: ImageFont.FreeTypeFont,
    x: float,
    y: float,
    target_h: int,
    color: str = TEXT_COLOR,
):
    """Draw a single line of text with inline emoji support.

    Emoji characters are rendered via Noto Color Emoji (bitmap, 109px)
    then scaled and composited onto the image.
    """
    if not _has_emoji(line):
        draw.text((x, y), line, font=font, fill=color)
        return

    cur_x = x
    for segment, is_emoji in _split_emoji_segments(line):
        if is_emoji:
            emoji_img = _render_emoji_image(segment, target_h)
            if emoji_img:
                # Vertically center emoji relative to text baseline
                ey = int(y)
                img.paste(emoji_img, (int(cur_x), ey), emoji_img)
                cur_x += emoji_img.width + 4
            else:
                # Fallback: render as regular text
                draw.text((cur_x, y), segment, font=font, fill=color)
                cur_x += font.getlength(segment)
        else:
            draw.text((cur_x, y), segment, font=font, fill=color)
            cur_x += font.getlength(segment)


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    area_top: int,
    area_bottom: int,
    color: str = TEXT_COLOR,
    img: Image.Image | None = None,
):
    """Draw multiline text centered horizontally and vertically in the given area.

    If img is provided and text contains emoji, emoji are composited onto img.
    """
    # Calculate line height
    bbox = font.getbbox("Аy|")
    line_height = bbox[3] - bbox[1]
    spacing = int(line_height * 0.3)

    max_width = SLIDE_WIDTH - 2 * MARGIN_X
    lines = _wrap_text(text, font, max_width, target_h=line_height)

    total_height = len(lines) * line_height + (len(lines) - 1) * spacing

    # Vertical centering
    area_height = area_bottom - area_top
    y_start = area_top + (area_height - total_height) // 2

    has_emoji = img is not None and _has_emoji(text)

    for i, line in enumerate(lines):
        line_width = _measure_line_width(line, font, line_height)
        x = (SLIDE_WIDTH - line_width) / 2
        y = y_start + i * (line_height + spacing)

        if has_emoji:
            _draw_line_with_emoji(img, draw, line, font, x, y, line_height, color)
        else:
            draw.text((x, y), line, font=font, fill=color)


# ── Slide renderers ──────────────────────────────────────────────────────────


def _create_slide() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Create a blank slide with base template (RGBA for emoji compositing)."""
    img = Image.new("RGBA", (SLIDE_WIDTH, SLIDE_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)
    _draw_base_template(draw)
    return img, draw


COVER_PHOTO_PATH = os.path.join(os.path.dirname(__file__), "zastavka_karusel.jpg")
COVER_OVERLAY_COLOR = (0, 0, 0, 140)  # semi-transparent black overlay
COVER_TEXT_COLOR = "#FFFFFF"


def render_slide_cover(headline: str, output_path: str) -> str:
    """Render slide 1: photo background with headline overlay."""
    # Load and resize cover photo to fit slide
    if os.path.exists(COVER_PHOTO_PATH):
        photo = Image.open(COVER_PHOTO_PATH).convert("RGBA")
        # Crop to 4:5 aspect ratio (center crop)
        target_ratio = SLIDE_WIDTH / SLIDE_HEIGHT
        photo_ratio = photo.width / photo.height
        if photo_ratio > target_ratio:
            # Photo is wider — crop sides
            new_w = int(photo.height * target_ratio)
            left = (photo.width - new_w) // 2
            photo = photo.crop((left, 0, left + new_w, photo.height))
        else:
            # Photo is taller — crop top/bottom
            new_h = int(photo.width / target_ratio)
            top = (photo.height - new_h) // 2
            photo = photo.crop((0, top, photo.width, top + new_h))
        photo = photo.resize((SLIDE_WIDTH, SLIDE_HEIGHT), Image.LANCZOS)

        # Add dark overlay for text readability
        overlay = Image.new("RGBA", (SLIDE_WIDTH, SLIDE_HEIGHT), COVER_OVERLAY_COLOR)
        img = Image.alpha_composite(photo, overlay)
    else:
        # Fallback: plain background
        img = Image.new("RGBA", (SLIDE_WIDTH, SLIDE_HEIGHT), BG_COLOR)

    draw = ImageDraw.Draw(img)
    font = _get_font("evolventa_bold", COVER_FONT_SIZE)
    _draw_centered_text(draw, headline, font, TEXT_AREA_TOP, TEXT_AREA_BOTTOM,
                        color=COVER_TEXT_COLOR, img=img)
    img.convert("RGB").save(output_path, "JPEG", quality=95)
    return output_path


def render_slide_content(point_text: str, output_path: str) -> str:
    """Render slides 2-6: numbered content point."""
    img, draw = _create_slide()
    font = _get_font("evolventa_bold", CONTENT_FONT_SIZE)
    _draw_centered_text(draw, point_text, font, TEXT_AREA_TOP, TEXT_AREA_BOTTOM, img=img)
    img.convert("RGB").save(output_path, "JPEG", quality=95)
    return output_path


def render_slide_cta(cta_text: str, output_path: str) -> str:
    """Render slide 7: CTA with emoji prefix."""
    img, draw = _create_slide()
    font = _get_font("evolventa_bold", CTA_FONT_SIZE)
    full_text = f"‼️ {cta_text}"
    _draw_centered_text(draw, full_text, font, TEXT_AREA_TOP, TEXT_AREA_BOTTOM, img=img)
    img.convert("RGB").save(output_path, "JPEG", quality=95)
    return output_path


def render_slide_ps(ps_text: str, output_path: str) -> str:
    """Render slide 8: P.S. text."""
    img, draw = _create_slide()
    font = _get_font("evolventa_bold", PS_FONT_SIZE)
    full_text = f"P.s.: {ps_text}"
    _draw_centered_text(draw, full_text, font, TEXT_AREA_TOP, TEXT_AREA_BOTTOM, img=img)
    img.convert("RGB").save(output_path, "JPEG", quality=95)
    return output_path


def render_carousel(carousel: dict, output_dir: str) -> list[str]:
    """Render all 8 slides for a carousel, return list of image paths.

    Args:
        carousel: Dict with keys: headline, points (list of 5), cta_text, ps_text.
        output_dir: Directory to save slide images.

    Returns:
        List of 8 image file paths.
    """
    os.makedirs(output_dir, exist_ok=True)

    carousel_id = carousel.get("id", "carousel")
    paths = []

    # Slide 1: Cover
    path = os.path.join(output_dir, f"{carousel_id}_1_cover.jpg")
    render_slide_cover(carousel["headline"], path)
    paths.append(path)

    # Slides 2-6: Content points
    for i, point in enumerate(carousel["points"][:5]):
        path = os.path.join(output_dir, f"{carousel_id}_{i + 2}_point.jpg")
        render_slide_content(point, path)
        paths.append(path)

    # Slide 7: CTA
    path = os.path.join(output_dir, f"{carousel_id}_7_cta.jpg")
    render_slide_cta(carousel.get("cta_text", ""), path)
    paths.append(path)

    # Slide 8: P.S.
    path = os.path.join(output_dir, f"{carousel_id}_8_ps.jpg")
    render_slide_ps(carousel.get("ps_text", ""), path)
    paths.append(path)

    return paths
