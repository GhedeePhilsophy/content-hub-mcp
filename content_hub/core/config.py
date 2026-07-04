"""core.config — content-agnostic configuration shared by every workflow.

Only the settings common to all Content Hub workflows live here: env loading,
Google credentials/paths, the scratch dir for generated media, and the brand-wide
generation defaults. Content-type specifics (the Social Calendar naming, folder
layout, aspect-ratio rules, …) live in that workflow's package, e.g. social.rules.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root = the folder that holds the content_hub package (content_hub/core/ -> up 2).
REPO_ROOT = Path(__file__).resolve().parents[2]


# --- .env ------------------------------------------------------------------
def load_dotenv(root: Path = REPO_ROOT) -> None:
    """Minimal .env loader (no dependency). Only sets vars not already in env."""
    env_path = root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def gemini_api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


# --- Google Drive credentials (OAuth "as you") -----------------------------
def credentials_path() -> Path:
    return REPO_ROOT / (os.environ.get("GDRIVE_CREDENTIALS_FILE") or "credentials.json")


def token_path() -> Path:
    return REPO_ROOT / (os.environ.get("GDRIVE_TOKEN_FILE") or "token.json")


# --- media generation ------------------------------------------------------
def generated_dir() -> Path:
    """Scratch dir for freshly generated media before upload. Override GENERATED_DIR."""
    return Path(os.environ.get("GENERATED_DIR") or (REPO_ROOT / "generated"))


DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"
DEFAULT_VIDEO_MODEL = "veo-3.1-generate-preview"


def brand_font_path() -> str | None:
    """TTF/OTF font for on-image text overlays. Set BRAND_FONT_PATH to the brand
    font; if unset, the overlay falls back to a bundled serif (see core.textcard)."""
    return os.environ.get("BRAND_FONT_PATH")


def video_model_override() -> str | None:
    """Default video model override for every run (env VIDEO_MODEL). Overrides the
    sheet's AI Model; the per-run --video-model flag still beats this."""
    return os.environ.get("VIDEO_MODEL")


def image_model_override() -> str | None:
    """Default image model override for every run (env IMAGE_MODEL). Overrides the
    sheet's AI Model; the per-run --image-model flag still beats this."""
    return os.environ.get("IMAGE_MODEL")


def video_duration_override() -> int | None:
    """Default target video length in seconds for every run (env VIDEO_DURATION).
    Veo builds clips >8s by chaining extensions. The --video-duration flag beats this."""
    raw = os.environ.get("VIDEO_DURATION")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None

# Brand imagery guardrails — the same for every content type (blog / social / email).
DEFAULT_NEGATIVE_PROMPT = (
    "text, watermark, logos, stock-wellness imagery (yoga poses, crystals, lotus, "
    "incense, chakra graphics), religious iconography"
)
