"""core.textcard — composite on-image text onto a generated slide.

AI image models render specific words unreliably (gibberish on longer phrases), so
we ask the model for clean artwork with negative space and stamp the exact wording
here with Pillow — correct every time, in the brand's serif and colours.

Content-agnostic: give it an image path and a string. The social carousel workflow
uses it, but blog/email could too.
"""

from __future__ import annotations

from pathlib import Path

# Brand palette (approximate RGB): Ivory text over a Deep-Forest-Green bottom scrim.
IVORY = (244, 239, 225)
FOREST = (24, 46, 35)
SHADOW = (12, 20, 15)

# Fallback serif chain if BRAND_FONT_PATH is unset (Windows system fonts, then PIL's).
_FONT_FALLBACKS = [
    r"C:\Windows\Fonts\georgia.ttf",
    r"C:\Windows\Fonts\cambria.ttc",
    r"C:\Windows\Fonts\times.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]


def _load_font(font_path: str | None, size: int):
    from PIL import ImageFont
    for candidate in ([font_path] if font_path else []) + _FONT_FALLBACKS:
        if candidate and Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
    return ImageFont.load_default(size)  # last resort; still legible, not on-brand


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    """Greedy word-wrap to fit max_width (px)."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def overlay_text(image_path: Path, text: str, *, font_path: str | None = None,
                 text_color: tuple = IVORY, scrim_color: tuple = FOREST) -> Path:
    """Draw ``text`` centered in the lower third of the image over a soft scrim.
    Overwrites the file in place. Returns the path."""
    from PIL import Image, ImageDraw
    text = (text or "").strip()
    if not text:
        return image_path

    img = Image.open(image_path).convert("RGB")
    W, H = img.size
    font = _load_font(font_path, max(24, min(80, int(W * 0.058))))
    pad = int(W * 0.08)

    measure = ImageDraw.Draw(img)
    lines = _wrap(measure, text, font, W - 2 * pad)
    asc, desc = font.getmetrics()
    line_h = int((asc + desc) * 1.25)
    block_h = line_h * len(lines)
    block_bottom = H - int(H * 0.09)
    block_top = block_bottom - block_h

    # Bottom scrim: forest-green fading up from the image bottom for legibility.
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(scrim)
    scrim_top = max(0, block_top - int(H * 0.06))
    for y in range(scrim_top, H):
        a = int(190 * (y - scrim_top) / max(1, H - scrim_top))
        sdraw.line([(0, y), (W, y)], fill=(*scrim_color, a))
    img = Image.alpha_composite(img.convert("RGBA"), scrim).convert("RGB")

    draw = ImageDraw.Draw(img)
    y = block_top
    for ln in lines:
        w = draw.textlength(ln, font=font)
        x = (W - w) / 2
        draw.text((x + 2, y + 2), ln, font=font, fill=SHADOW)   # soft shadow
        draw.text((x, y), ln, font=font, fill=text_color)
        y += line_h

    img.save(image_path)
    return image_path
