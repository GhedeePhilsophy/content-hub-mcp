"""exporters.metricool — Metricool bulk-import CSV.

Metricool's importer keys on exact header spelling and column order, so [COLUMNS]
mirrors its template verbatim. If Metricool revises the template, point `template=`
at the new file and the header is taken from there instead — the mapping below only
ever addresses columns by name, so extra or reordered columns cost nothing.

Every post is written as a Metricool DRAFT unless `schedule=True`, so an import
can't auto-publish before a human has seen the media previews resolve.
"""

from __future__ import annotations

import csv
from pathlib import Path

NAME = "metricool"
EXTENSION = ".csv"
MAX_SLIDES = 10        # Picture Url 1..10

# Which Drive URL form to put in the media columns (see exporters.LINK_STYLES).
# Metricool rejected the direct-download form in testing even though it serves correct
# bytes, which suggests it resolves Drive links through its own Google integration
# rather than fetching them — so it wants the canonical share link.
LINK_STYLE = "share"

# Our Platform value -> Metricool's boolean channel column.
CHANNEL = {"Instagram": "Instagram", "Facebook": "Facebook", "Tiktok": "TikTok"}

_MEDIA = [f"Picture Url {i}" for i in range(1, MAX_SLIDES + 1)]
_ALT = [f"Alt text picture {i}" for i in range(1, MAX_SLIDES + 1)]

COLUMNS = (
    "Text", "Date", "Time", "Draft",
    "Facebook", "Twitter/X", "LinkedIn", "GBP", "Instagram", "Pinterest",
    "TikTok", "Youtube", "Threads", "Bluesky",
    *_MEDIA, *_ALT,
    "Document title", "Shortener", "Video Thumbnail Url", "Video Cover Frame",
    "Twitter/X Can reply", "Twitter/X Type", "Twitter/X Poll Duration minutes",
    "Twitter/X Poll Option 1", "Twitter/X Poll Option 2",
    "Twitter/X Poll Option 3", "Twitter/X Poll Option 4",
    "Pinterest Board", "Pinterest Pin Title", "Pinterest Pin Link",
    "Pinterest Pin New Format",
    "Instagram Post Type", "Instagram Show Reel On Feed",
    "Youtube Video Title", "Youtube Video Type", "Youtube Video Privacy",
    "Youtube video for kids", "Youtube Video Category", "Youtube Video Tags",
    "Youtube playlist",
    "GBP Post Type",
    "Facebook Post Type", "Facebook Title",
    "First Comment Text",
    "TikTok Title", "TikTok disable comments", "TikTok disable duet",
    "TikTok disable stitch", "TikTok Post Privacy", "TikTok Branded Content",
    "TikTok Your Brand", "TikTok Auto Add Music", "TikTok Photo Cover Index",
    "TikTok musicId", "TikTok music title", "TikTok music author",
    "TikTok music previewUrl", "TikTok music thumbnailUrl",
    "TikTok music soundVolume", "TikTok music originalVolume",
    "TikTok music startMillis", "TikTok music endMillis",
    "TikTok Ai generated content",
    "LinkedIn Type", "LinkedIn Poll Question",
    "LinkedIn Poll Option 1", "LinkedIn Poll Option 2",
    "LinkedIn Poll Option 3", "LinkedIn Poll Option 4",
    "LinkedIn Poll Duration", "LinkedIn Show link preview",
    "LinkedIn Images as Carousel",
    "Threads Reply Control", "Threads Is Spoiler", "Threads Post Type",
    "Brand name",
)


def _post_type(post) -> str:
    """Metricool's per-channel post type. A carousel is an ordinary POST with several
    pictures — the multiple Picture Url columns are what make it a carousel."""
    return "REEL" if post.fmt == "Reel" else "POST"


def to_row(post, columns: list[str], *, schedule: bool = False) -> dict:
    """One [Post] -> one Metricool CSV row keyed by column name."""
    row = {c: "" for c in columns}
    row["Text"] = post.caption
    row["Date"] = post.date
    row["Time"] = post.time
    row["Draft"] = "false" if schedule else "true"

    for col in CHANNEL.values():
        if col in row:
            row[col] = "false"
    channel = CHANNEL.get(post.platform)
    if not channel:
        raise ValueError(f"{post.row_id}: no Metricool channel for platform "
                         f"{post.platform!r}")
    row[channel] = "true"

    for i, url in enumerate(post.media[:MAX_SLIDES], 1):
        row[f"Picture Url {i}"] = url
        # Headline doubles as alt text — better for accessibility than leaving it blank.
        row[f"Alt text picture {i}"] = post.headline

    if channel == "Instagram":
        row["Instagram Post Type"] = _post_type(post)
        row["First Comment Text"] = post.hashtags
    elif channel == "Facebook":
        row["Facebook Post Type"] = _post_type(post)
    elif channel == "TikTok":
        row["TikTok Title"] = post.headline[:90]
    return row


def write(posts, out_path: Path, *, schedule: bool = False,
          template: str | None = None) -> dict:
    """Write the CSV. Returns target-specific detail merged into the export result."""
    if template:
        with Path(template).open(newline="", encoding="utf-8-sig") as fh:
            columns = next(csv.reader(fh))
    else:
        columns = list(COLUMNS)

    rows = [to_row(p, columns, schedule=schedule) for p in posts]
    # utf-8 (no BOM): captions carry real curly quotes and em dashes. If Metricool
    # ever renders them as mojibake, switch to utf-8-sig.
    with Path(out_path).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return {"files": [str(out_path)],
            "columns": len(columns), "header_source": template or "built-in"}
