"""social.rules — the Social Calendar domain rules.

Everything here is specific to the social workflow (file naming, the Drive folder
layout, the calendar-row -> media-job mapping). Blog and email get their own
sibling rules modules. Pure/deterministic (no I/O) so it's unit-testable offline.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..core.config import REPO_ROOT


# --- filesystem + Drive roots ----------------------------------------------
def calendar_dir() -> Path:
    """Where local calendar .xlsx working copies live. Override with CALENDAR_DIR."""
    return Path(os.environ.get("CALENDAR_DIR") or REPO_ROOT)


def social_calendar_root_id() -> str | None:
    """Drive folder id of the evergreen 'Social Calendar' root (holds the quarters)."""
    return os.environ.get("SOCIAL_CALENDAR_ROOT_ID")


def social_calendar_mock_root_id() -> str | None:
    """Optional Drive folder id used as the root for --mock rehearsal uploads, so
    placeholder files never land in (or overwrite) the production quarter folders.
    If unset, mock uploads go to a '_mock rehearsal' subfolder under the quarter."""
    return os.environ.get("SOCIAL_CALENDAR_MOCK_ROOT_ID")


# --- calendar naming -------------------------------------------------------
CALENDAR_PREFIX = "Ghedee_Social_Calendar"


def calendar_filename(calendar_id: str, version: int) -> str:
    return f"{CALENDAR_PREFIX}_{calendar_id}_v{version}.xlsx"


# Ghedee_Social_Calendar_<id>_v<n>.xlsx  ->  (id, n)
_FILENAME_RE = re.compile(
    rf"^{re.escape(CALENDAR_PREFIX)}_(?P<id>.+)_v(?P<v>\d+)\.xlsx$", re.IGNORECASE
)


def parse_calendar_filename(name: str) -> tuple[str, int] | None:
    m = _FILENAME_RE.match(name)
    if not m:
        return None
    return m.group("id"), int(m.group("v"))


# Q3_2026 / q3-2026 / Q3 2026  ->  "Q3 2026" (the quarter subfolder under the root)
_QUARTER_RE = re.compile(r"^Q([1-4])[ _-]?(\d{4})$", re.IGNORECASE)


def quarter_folder_for(calendar_id: str) -> str | None:
    """Derive the Drive quarter subfolder name from a Calendar ID.

    Handles the quarter form (``Q3_2026`` -> ``Q3 2026``). A Calendar ID that is
    a month or a date range can't be resolved to a quarter unambiguously; those
    callers must pass an explicit quarter_folder override.
    """
    m = _QUARTER_RE.match(calendar_id.strip())
    if m:
        return f"Q{m.group(1)} {m.group(2)}"
    return None


# --- Drive asset structure (Ghedee Social Drive asset-structure doc) --------
SUBFOLDER_DOCS = "00_Calendar & Docs"
SUBFOLDER_IMAGES = ["02_AI Visuals", "Images"]
SUBFOLDER_VIDEO = ["02_AI Visuals", "Video"]
SUBFOLDER_CAROUSELS = ["03_Carousels"]  # <group> is appended


# --- domain rules: calendar row -> media job -------------------------------
# Visual Type strings as they appear in the sheet's "Visual Type" column.
VT_IMAGE = "AI text-to-image"
VT_VIDEO = "AI text-to-video"
VT_RECORDED = "Recorded video of Wiah"  # out of scope for generation


@dataclass(frozen=True)
class VisualPlan:
    """The resolved generation shape for one calendar row."""

    kind: str          # "image" | "video" | "carousel"
    aspect_ratio: str  # "1:1" | "3:4" | "16:9" ...
    generate: bool     # False for recorded / unrecognised -> skip, don't error
    reason: str = ""   # why it was skipped (when generate is False)


def plan_visual(visual_type: str | None, fmt: str | None) -> VisualPlan:
    """Resolve a row's Visual Type + Format into what (and how) to generate.

    Rules (Social Calendar Cowork prompt, MEDIA GENERATION section):
      - AI text-to-video  -> 16:9 single video (the 6 hero rows).
      - AI text-to-image + Format 'Carousel' -> 3:4 carousel (multi-slide).
      - AI text-to-image, any other format   -> 1:1 single image.
      - Recorded video of Wiah / anything else -> skip (not generated here).

    Note: Format alone is unreliable (the video rows also read 'Single image /
    carousel'), so Visual Type is the primary key and Format only distinguishes
    the carousel case for image rows.
    """
    vt = (visual_type or "").strip()
    f = (fmt or "").strip().lower()
    if vt == VT_VIDEO:
        return VisualPlan(kind="video", aspect_ratio="16:9", generate=True)
    if vt == VT_IMAGE:
        # A true multi-slide carousel is Format == 'Carousel' exactly; the mixed
        # 'Single image / carousel' string is treated as a single feed image.
        if f == "carousel":
            # 4:5 is Instagram's tallest feed/carousel ratio (uncropped). The model
            # can't render 4:5 directly, so core.media generates 3:4 and crops to it.
            return VisualPlan(kind="carousel", aspect_ratio="4:5", generate=True)
        return VisualPlan(kind="image", aspect_ratio="1:1", generate=True)
    if vt == VT_RECORDED:
        return VisualPlan(kind="skip", aspect_ratio="", generate=False,
                          reason="recorded clip — needs a film shoot, not generation")
    return VisualPlan(kind="skip", aspect_ratio="", generate=False,
                      reason=f"unrecognised Visual Type {vt!r}")


PLATFORM_SHORT = {"instagram": "IG", "tiktok": "TT", "facebook": "FB"}


def platform_short(platform: str | None, row_id: str | None = None) -> str:
    """Short platform code for the filename stem. Prefer the Row ID's own segment
    (e.g. 27JUL-IG-01 -> IG) since it already encodes IG/IGR/TT/FB; fall back to
    the Platform column."""
    if row_id:
        parts = row_id.split("-")
        if len(parts) >= 2 and parts[1].isalpha():
            return parts[1].upper()
    key = (platform or "").strip().lower().split()[0] if platform else ""
    return PLATFORM_SHORT.get(key, "GEN")


_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def slugify(text: str, max_words: int = 3) -> str:
    """Filename-safe short slug from a hook/pillar, e.g. 'The Universal Law of
    Creation' -> 'Universal-Law-Creation'. Purely cosmetic — the Row ID is the
    stable key the existence check keys on, so editing the hook never orphans
    an already-generated file."""
    words = [w for w in _SLUG_RE.split(text or "") if w]
    stop = {"the", "a", "an", "of", "and", "to", "for"}
    kept = [w for w in words if w.lower() not in stop][:max_words] or words[:max_words]
    return "-".join(kept) or "asset"
