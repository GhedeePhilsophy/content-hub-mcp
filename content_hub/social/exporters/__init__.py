"""social.exporters — turn the living calendar into a scheduler's bulk-import file.

One neutral read of the sheet, many targets. Reading the live sheet, picking which
rows are publishable, and resolving each row's Drive asset into a URL a third party
can actually fetch are all the *same* problem whatever the scheduler is — so they
live here, and a target module only maps a [Post] onto its own column layout.

  metricool   Metricool bulk-import CSV

Adding a target = a module exposing NAME, EXTENSION and ``write(posts, out, **opts)``,
registered in [TARGETS]. It receives fully-resolved [Post] objects and never touches
Drive or the sheet.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from . import metricool, publer
from .. import edit_ops, sheet_ops
from ...core.drive import file_id_from_link

# Statuses that mean "this post is finished and may be handed to a scheduler".
# Draft is deliberately NOT here — pass it explicitly to export an unapproved row.
DEFAULT_STATUSES = ("Approved",)

TARGETS = {metricool.NAME: metricool, publer.NAME: publer}


@dataclass
class Post:
    """One publishable calendar row, scheduler-agnostic."""
    row_id: str
    status: str
    date: str            # YYYY-MM-DD
    time: str            # HH:MM:SS, as authored (see the timezone note in export())
    platform: str        # Instagram | Facebook | Tiktok
    fmt: str             # Post | Reel | Carousel
    headline: str
    caption: str
    hashtags: str
    media: list[str] = field(default_factory=list)   # direct URLs, carousel expanded
    is_video: bool = False

    @property
    def is_carousel(self) -> bool:
        return len(self.media) > 1


# Drive link forms, and what each actually returns to an anonymous fetcher (measured,
# not assumed). Which one a scheduler wants depends on whether it FETCHES the URL for
# bytes or PARSES it as a Drive share link and resolves the file through its own Google
# integration — so this is a per-target setting, not a single right answer.
#
#   share     https://drive.google.com/file/d/<id>/view?usp=sharing
#             200 text/html — the viewer page, NOT the asset. Useless to a fetcher, but
#             it is the canonical share link a scheduler's Drive integration expects.
#   download  https://drive.usercontent.google.com/download?id=<id>&export=download
#             200 with the true content-type for both images and video. The right answer
#             for anything that fetches bytes. Note the URL carries no file extension,
#             which some uploaders validate on.
#   uc        https://drive.google.com/uc?export=download&id=<id>
#             303 with an EMPTY body, redirecting to `download`. A fetcher that doesn't
#             follow redirects sees nothing at all.
#   lh3       https://lh3.googleusercontent.com/d/<id>
#             200 — but for a VIDEO it serves a poster JPEG instead of the mp4. Fails
#             silently: the post publishes as a still frame and nothing looks broken.
#             Never use this where video is possible.
LINK_STYLES = {
    "share": "https://drive.google.com/file/d/{id}/view?usp=sharing",
    "download": "https://drive.usercontent.google.com/download?id={id}&export=download",
    "uc": "https://drive.google.com/uc?export=download&id={id}",
    "lh3": "https://lh3.googleusercontent.com/d/{id}",
}
DEFAULT_LINK_STYLE = "share"


def media_url(file_id: str, style: str = DEFAULT_LINK_STYLE) -> str:
    """Build a media URL for `file_id` in one of the [LINK_STYLES].

    Every style needs the file to be link-shared; this module never changes Drive
    permissions. If sharing is later tightened, media stops resolving for every
    already-scheduled post.
    """
    try:
        return LINK_STYLES[style].format(id=file_id)
    except KeyError:
        raise ValueError(f"unknown link style {style!r}; "
                         f"known: {', '.join(sorted(LINK_STYLES))}") from None


def _as_date(v) -> str:
    if hasattr(v, "date"):
        return v.date().isoformat()
    return dt.date.fromisoformat(str(v).strip()[:10]).isoformat()


def _as_time(v) -> str:
    """'5:00 AM' or a time object -> '05:00:00'."""
    if hasattr(v, "strftime") and not isinstance(v, str):
        return v.strftime("%H:%M:%S")
    return dt.datetime.strptime(str(v).strip().upper(), "%I:%M %p").strftime("%H:%M:%S")


def collect_posts(calendar_id: str, *, statuses: tuple[str, ...] = DEFAULT_STATUSES,
                  max_slides: int = 10,
                  link_style: str = DEFAULT_LINK_STYLE) -> tuple[list[Post], list[str]]:
    """Read the live sheet -> (posts, warnings).

    A row is skipped (with a warning, never silently) when it has no asset, an
    unparseable link, or a carousel folder that disagrees with its Slides count.
    """
    drive, _sid, _tab, cal, _link = edit_ops._load_live(calendar_id)
    ws = cal.ws
    idx = {ws.cell(1, c).value: c for c in range(1, ws.max_column + 1) if ws.cell(1, c).value}

    def get(r: int, name: str) -> str:
        return str(ws.cell(r, idx[name]).value or "").strip() if name in idx else ""

    posts: list[Post] = []
    warnings: list[str] = []
    for r in range(2, ws.max_row + 1):
        row_id, status = get(r, "Row ID"), get(r, "Status")
        if not row_id or status not in statuses:
            continue
        # Generated Asset Link is the source of truth: a bring-your-own asset is
        # copied into that slot by generate, so it is always the published file.
        asset = get(r, "Generated Asset Link") or get(r, "Created Asset Link")
        if not asset:
            warnings.append(f"{row_id}: no asset link — skipped")
            continue
        fid = file_id_from_link(asset)
        if not fid:
            warnings.append(f"{row_id}: unparseable Drive link — skipped")
            continue

        fmt = get(r, "Format")
        if fmt == "Carousel":
            slides = [f for f in drive.list_children(fid)
                      if f.get("mimeType", "").startswith("image/")]
            slides.sort(key=lambda f: f["name"])   # slide-1..N
            want = int(float(get(r, "Slides") or 0))
            if want and len(slides) != want:
                warnings.append(
                    f"{row_id}: Slides={want} but the folder holds {len(slides)} "
                    "images — skipped")
                continue
            if len(slides) > max_slides:
                warnings.append(
                    f"{row_id}: {len(slides)} slides exceeds the target's "
                    f"{max_slides}-image limit — skipped")
                continue
            media = [media_url(f["id"], link_style) for f in slides]
            is_video = False
        else:
            media = [media_url(fid, link_style)]
            is_video = "video" in get(r, "Visual Type").lower()

        posts.append(Post(
            row_id=row_id, status=status,
            date=_as_date(ws.cell(r, idx["Date"]).value),
            time=_as_time(ws.cell(r, idx["Time (PT)"]).value),
            platform=get(r, "Platform"), fmt=fmt,
            headline=get(r, "Headline"), caption=get(r, "Caption"),
            hashtags=get(r, "First-comment Hashtags (IG)"),
            media=media, is_video=is_video,
        ))
    return posts, warnings


def export(calendar_id: str, *, target: str = metricool.NAME, out_path: str | None = None,
           statuses: tuple[str, ...] = DEFAULT_STATUSES, schedule: bool = False,
           template: str | None = None,
           link_style: str | None = None) -> dict:
    """Write a scheduler import file for `calendar_id`.

    `schedule=False` (the default) marks every post as a DRAFT in the target, so an
    import can never auto-publish; the team schedules from inside the scheduler once
    the media previews look right.

    `link_style` picks which Drive URL form the media columns carry (see [LINK_STYLES]);
    it defaults to the target's own LINK_STYLE. Which form works is a property of the
    scheduler, so it is worth re-testing with one post rather than assumed.

    Times are exported exactly as authored in `Time (PT)`. The target account's
    timezone must be Pacific or the whole calendar lands hours off — no conversion
    is applied here because the sheet doesn't record the destination's timezone.
    """
    mod = TARGETS.get(target)
    if not mod:
        raise ValueError(f"unknown export target {target!r}; "
                         f"known: {', '.join(sorted(TARGETS))}")

    style = link_style or getattr(mod, "LINK_STYLE", DEFAULT_LINK_STYLE)
    posts, warnings = collect_posts(
        calendar_id, statuses=statuses, max_slides=getattr(mod, "MAX_SLIDES", 10),
        link_style=style)
    # `out` is a BASE path. A single-file target writes exactly it; a target that fans
    # out (e.g. one file per platform, as Publer needs) derives its names from the stem
    # and returns them all in `files`.
    out = Path(out_path) if out_path else Path("generated") / calendar_id / (
        f"{sheet_ops.live_sheet_name(calendar_id)}_{mod.NAME}{mod.EXTENSION}")
    out.parent.mkdir(parents=True, exist_ok=True)

    written = mod.write(posts, out, schedule=schedule, template=template)
    files = written.pop("files", [str(out)])

    by_platform: dict[str, int] = {}
    for p in posts:
        by_platform[p.platform] = by_platform.get(p.platform, 0) + 1
    return {
        "calendar_id": calendar_id,
        "target": mod.NAME,
        "files": files,
        "exported": len(posts),
        "as_draft": not schedule,
        "link_style": style,
        "statuses": list(statuses),
        "by_platform": by_platform,
        "images": sum(1 for p in posts if not p.is_video and not p.is_carousel),
        "videos": sum(1 for p in posts if p.is_video),
        "carousels": sum(1 for p in posts if p.is_carousel),
        "slides": sum(len(p.media) for p in posts if p.is_carousel),
        "skipped": len(warnings),
        "warnings": warnings,
        **written,
    }
