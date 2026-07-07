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
    """Drive folder id of the evergreen 'Social Calendar' root (holds the calendars)."""
    return os.environ.get("SOCIAL_CALENDAR_ROOT_ID")


def social_calendar_mock_root_id() -> str | None:
    """Optional Drive folder id used as the root for --mock rehearsal uploads, so
    placeholder files never land in (or overwrite) the production calendar folders.
    If unset, mock uploads go to a '_mock rehearsal' subfolder under the calendar folder."""
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


def calendar_folder(calendar_id: str) -> str:
    """The Drive subfolder (under the Social Calendar root) that holds this calendar.
    The folder is named by the Calendar ID verbatim — a quarter (``Q3_2026``), a date
    range, or a single day are all just folder names, with no derivation."""
    return calendar_id.strip()


# --- Drive asset structure (Ghedee Social Drive asset-structure doc) --------
SUBFOLDER_DOCS = "00_Calendar & Docs"
SUBFOLDER_WIAH_VIDEOS = ["01_Wiah Videos"]  # Wiah's recorded clips (copied, never generated)
SUBFOLDER_IMAGES = ["02_AI Visuals", "Images"]
SUBFOLDER_VIDEO = ["02_AI Visuals", "Video"]
SUBFOLDER_CAROUSELS = ["03_Carousels"]  # <group> is appended


# --- domain rules: calendar row -> media job -------------------------------
# Visual Type strings as they appear in the sheet's "Visual Type" column.
VT_IMAGE = "AI text-to-image"
VT_VIDEO = "AI text-to-video"
VT_CAROUSEL = "AI text-to-carousel"  # Format 'Carousel' rows use this Visual Type
VT_RECORDED = "Recorded video of Wiah"  # out of scope for generation


@dataclass(frozen=True)
class VisualPlan:
    """The resolved generation shape for one calendar row."""

    kind: str          # "image" | "video" | "carousel"
    aspect_ratio: str  # "1:1" | "3:4" | "16:9" ...
    generate: bool     # False for recorded / unrecognised -> skip, don't error
    reason: str = ""   # why it was skipped (when generate is False)
    recorded: bool = False  # Wiah's own clip: never AI-generated, only copied from a
    #                         Selected Asset into 01_Wiah Videos (kind stays "video")


def plan_visual(visual_type: str | None, fmt: str | None) -> VisualPlan:
    """Resolve a row's Visual Type + Format into what (and how) to generate.

    Rules (Social Calendar Cowork prompt, MEDIA GENERATION section):
      - AI text-to-carousel (Format 'Carousel') -> 4:5 carousel (multi-slide).
      - AI text-to-video -> single video; Format 'Reel' is vertical 9:16, else 16:9.
      - AI text-to-image  -> 1:1 single image.
      - Recorded video of Wiah -> a video that is never AI-generated: it's copied
        from the Created Asset column into 01_Wiah Videos (kind 'video', recorded).
      - anything else -> skip (not generated here).

    Visual Type is the primary key. The legacy 'AI text-to-image' + Format 'Carousel'
    combination is still accepted as a carousel for backward compatibility.
    """
    vt = (visual_type or "").strip()
    f = (fmt or "").strip().lower()
    if vt == VT_CAROUSEL or (vt == VT_IMAGE and f == "carousel"):
        # 4:5 is Instagram's tallest feed/carousel ratio. gpt-image-2 renders it
        # natively (1024x1280), so no crop is needed.
        return VisualPlan(kind="carousel", aspect_ratio="4:5", generate=True)
    if vt == VT_VIDEO:
        # Reels are vertical short-form (9:16); a Post video is a 16:9 feed clip.
        return VisualPlan(kind="video", aspect_ratio="9:16" if f == "reel" else "16:9",
                          generate=True)
    if vt == VT_IMAGE:
        return VisualPlan(kind="image", aspect_ratio="1:1", generate=True)
    if vt == VT_RECORDED:
        # Not AI-generated (generate=False), but a real video asset when a Selected
        # Asset is provided — the calendar reader gates it on that link.
        return VisualPlan(kind="video", aspect_ratio="9:16", generate=False,
                          recorded=True,
                          reason="recorded clip — awaiting a Selected Asset (no film yet)")
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
