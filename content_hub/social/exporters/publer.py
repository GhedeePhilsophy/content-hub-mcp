"""exporters.publer — Publer bulk-import CSV (https://app.publer.com/).

Publer's importer differs from Metricool's in two ways that shape this module:

* **No platform column.** A Publer CSV import applies *every* row to the *same*
  accounts you pick in the import wizard — there is no per-row account targeting. Our
  calendar mixes Instagram / Facebook / TikTok with different copy per platform, so a
  single combined file would cross-post (e.g. an Instagram caption to Facebook). This
  module therefore writes **one CSV per platform** and the operator imports each against
  the matching account.

* **Google Drive is a first-class media host.** Publer downloads the file itself via
  Drive's public-share resolution — its own error text keys on the word "sharing" — so it
  wants the canonical share link (``…/view?usp=sharing``), which is why LINK_STYLE below
  is "share". The file must be shared "Anyone with the link" or Publer reports
  "The provided URL is not a publicly accessible media." (Docs:
  publer.com/help → "Where do I host the media for the CSV file?".)

Columns follow Publer's 12-column template verbatim; the importer keys on their order,
so [COLUMNS] must not be reordered. Point `template=` at a newer template to override.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

NAME = "publer"
EXTENSION = ".csv"
LINK_STYLE = "share"    # Publer resolves Drive share links itself; see module docstring.
MAX_SLIDES = 10         # Media URL(s) is comma-separated; 10 matches IG's carousel cap.

# Publer's 12-column template, in order. Header text is matched verbatim by the importer.
COLUMNS = (
    "Date - Intl. format or prompt",
    "Text",
    "Link(s) - Separated by comma for FB carousels",
    "Media URL(s) - Separated by comma",
    "Title - For the video, pin, PDF ..",
    "Label(s) - Separated by comma",
    "Alt text(s) - Separated by ||",
    "Comment(s) - Separated by ||",
    "Pin board, FB album, or Google category",
    "Post subtype - I.e. story, reel, PDF ..",
    "CTA - For Facebook links or Google",
    "Reminder - For stories, reels, shorts, and TikToks",
)
_COL = {  # role -> exact header, so the mapping never repeats the verbose names
    "date": COLUMNS[0], "text": COLUMNS[1], "links": COLUMNS[2], "media": COLUMNS[3],
    "title": COLUMNS[4], "labels": COLUMNS[5], "alt": COLUMNS[6], "comments": COLUMNS[7],
    "board": COLUMNS[8], "subtype": COLUMNS[9], "cta": COLUMNS[10], "reminder": COLUMNS[11],
}

# Our Platform value -> a filename-safe slug for the per-platform file.
PLATFORM_SLUG = {"Instagram": "instagram", "Facebook": "facebook", "Tiktok": "tiktok"}


def _subtype(post) -> str:
    """Publer post subtype. Carousels are auto-detected from multiple Media URLs, and a
    16:9 video Post is an ordinary video — so only an explicit Reel needs labelling."""
    return "Reel" if post.fmt == "Reel" else ""


def to_row(post, columns: list[str], *, schedule: bool = False) -> dict:
    """One [Post] -> one Publer CSV row keyed by column name.

    `schedule` is accepted for a uniform target contract but has no cell to write: a
    Publer CSV has no draft flag — whether the import lands as drafts or scheduled posts
    is chosen in the import wizard, not the file. The `export` summary still reports it.
    """
    row = {c: "" for c in columns}
    # International format "YYYY-MM-DD HH:MM" (Publer accepts this alongside YYYY/MM/DD).
    row[_COL["date"]] = f"{post.date} {post.time[:5]}"
    row[_COL["text"]] = post.caption
    row[_COL["media"]] = ",".join(post.media)
    # One alt per image (headline stands in), joined with Publer's "||" separator.
    # No surrounding spaces: the spec token is literally "||", and if Publer doesn't
    # trim, padding would leak into the alt text.
    row[_COL["alt"]] = "||".join([post.headline] * len(post.media))
    row[_COL["subtype"]] = _subtype(post)
    # First-comment hashtags are Instagram-only in the sheet; only IG rows carry them.
    if post.platform == "Instagram" and post.hashtags:
        row[_COL["comments"]] = post.hashtags
    return row


def write(posts, out_path: Path, *, schedule: bool = False,
          template: str | None = None) -> dict:
    """Write one CSV per platform. Returns the file list + per-file counts.

    `out_path` is the base name; each platform's file inserts its slug before the
    extension (…_publer.csv -> …_publer_instagram.csv).
    """
    if template:
        with Path(template).open(newline="", encoding="utf-8-sig") as fh:
            columns = next(csv.reader(fh))
    else:
        columns = list(COLUMNS)

    base = Path(out_path)
    groups: dict[str, list] = {}
    for p in posts:
        if p.platform not in PLATFORM_SLUG:
            raise ValueError(f"{p.row_id}: no Publer file mapping for platform "
                             f"{p.platform!r}")
        groups.setdefault(p.platform, []).append(p)

    files, per_file = [], {}
    for platform, group in sorted(groups.items()):
        slug = PLATFORM_SLUG[platform]
        dest = base.with_name(f"{base.stem}_{slug}{base.suffix}")
        rows = [to_row(p, columns, schedule=schedule) for p in group]
        # utf-8 (no BOM): captions carry real curly quotes and em dashes.
        with dest.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
        files.append(str(dest))
        per_file[slug] = len(rows)

    return {"files": files, "columns": len(columns),
            "files_by_platform": per_file, "header_source": template or "built-in"}
