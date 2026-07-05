"""social.preview — a self-contained HTML review page of the calendar's posts.

Renders every scheduled post as a mockup in its platform's real chrome (Instagram
feed card, Facebook post, TikTok 9:16 with the action rail), grouped by week, so a
reviewer can approve the round in context. All post assets are read from Google
Drive, downscaled, JPEG-compressed, and inlined as data URIs, so the page is a
single portable file (no external assets) — safe to open locally, share, or upload
back to Drive. A per-calendar cache keyed by Drive md5 means a re-run only re-fetches
assets that actually changed.

Video posts show the clip's first frame (extracted via ffmpeg) with the play button
linking to the Drive clip; recorded-Wiah rows show a labelled placeholder.
"""

from __future__ import annotations

import base64
import html
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import rules
from ..core import config
from ..core.drive import FOLDER_MIME

# --- platform identity -----------------------------------------------------
_PLATFORMS = {
    "instagram": {"name": "wiah_at_ghedeephilosophy", "label": "Instagram"},
    "facebook": {"name": "Wiah at Ghedee Philosophy", "label": "Facebook"},
    "tiktok": {"name": "wiah_at_ghedeephilosophy", "label": "TikTok"},
}


def _platform_key(platform: str) -> str:
    p = (platform or "").lower()
    if "instagram" in p:
        return "instagram"
    if "facebook" in p:
        return "facebook"
    if "tiktok" in p:
        return "tiktok"
    return "instagram"


def _handle(platform: str, key: str) -> str:
    m = re.search(r"@[\w.]+", platform or "")
    if m:
        return m.group(0)
    return "@" + _PLATFORMS[key]["name"]


# --- image inlining --------------------------------------------------------
@dataclass
class ImageRef:
    """A pointer to one source image: a stable content ``key`` (so a cache can tell
    if it changed) and a ``fetch`` that returns its raw bytes only when needed."""
    key: str
    fetch: object  # callable () -> bytes


def _encode(raw: bytes, max_px: int, quality: int = 74) -> str | None:
    """Downscale to max_px on the long edge, JPEG-compress, return a data: URI."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return None
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _extract_first_frame(video_bytes: bytes) -> bytes | None:
    """First frame of an MP4 as PNG bytes, via imageio+ffmpeg. None if unavailable
    (dependency missing or decode error) -> caller falls back to a plain poster."""
    try:
        import imageio
    except ImportError:
        return None
    import os
    import tempfile
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.write(video_bytes)
        tmp.close()
        reader = imageio.get_reader(tmp.name)
        frame = reader.get_data(0)
        reader.close()
        from PIL import Image
        buf = io.BytesIO()
        Image.fromarray(frame).save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def _data_uri(ref: ImageRef, max_px: int, cache=None) -> str | None:
    ck = f"{ref.key}@{max_px}"
    produce = lambda: _encode(ref.fetch(), max_px)  # noqa: E731
    return cache.get_or_make(ck, produce) if cache is not None else produce()


def _video_poster_uri(ref: ImageRef, max_px: int, cache=None) -> str | None:
    """Data URI of the video's first frame (downscaled). Cached by the clip's content
    key so extraction/download happens once until the clip changes."""
    ck = f"{ref.key}@vframe@{max_px}"

    def produce():
        png = _extract_first_frame(ref.fetch())
        return _encode(png, max_px) if png else None

    return cache.get_or_make(ck, produce) if cache is not None else produce()


class _ImgCache:
    """Content-addressed cache of encoded thumbnails. A re-run reuses any entry whose
    Drive md5 (and size) is unchanged — so only assets that actually changed on Drive
    are re-downloaded and re-encoded."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {}
        self.requested: set = set()
        self.hits = self.misses = 0
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def get_or_make(self, ck: str, produce) -> str | None:
        self.requested.add(ck)
        if ck in self.data:
            self.hits += 1
            return self.data[ck]
        self.misses += 1
        uri = produce()
        if uri:
            self.data[ck] = uri
        return uri

    def save(self) -> None:
        # keep only what this run used, so the cache can't grow without bound
        self.data = {k: v for k, v in self.data.items() if k in self.requested}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data), encoding="utf-8")


def _drive_ref(drive, f: dict) -> ImageRef:
    tag = f.get("md5Checksum") or f.get("modifiedTime") or ""
    fid = f["id"]
    return ImageRef(f"drive:{fid}:{tag}", lambda: drive.download_bytes(fid))


# --- Drive asset lookup ----------------------------------------------------
class _DriveSource:
    """Pulls a row's assets from Google Drive (route-by-type folders), keyed on the
    Row ID prefix — mirrors the generate workflow's existence check. Each type folder
    is listed once; image bytes are downloaded on demand and inlined."""

    def __init__(self, drive, calendar_id: str, quarter_folder: str | None = None):
        self.drive = drive
        root = rules.social_calendar_root_id()
        if not root:
            raise RuntimeError("SOCIAL_CALENDAR_ROOT_ID is not set.")
        quarter = quarter_folder or rules.quarter_folder_for(calendar_id)
        if not quarter:
            raise RuntimeError(f"Could not derive a quarter folder from {calendar_id!r}; "
                               "pass quarter_folder.")
        self.calendar_id = calendar_id
        base = drive.find_folder_path(root, [quarter])
        if not base:
            raise FileNotFoundError(f"Drive folder {quarter!r} not found under the "
                                    "Social Calendar root.")
        self.images = self._list(base, rules.SUBFOLDER_IMAGES)
        self.videos = self._list(base, rules.SUBFOLDER_VIDEO)
        carousel_parent = drive.find_folder_path(base, rules.SUBFOLDER_CAROUSELS)
        self.carousels = drive.list_children(carousel_parent) if carousel_parent else []
        self.docs = drive.find_folder_path(base, [rules.SUBFOLDER_DOCS])

    def _list(self, base, subpath) -> list[dict]:
        fid = self.drive.find_folder_path(base, subpath) if base else None
        return self.drive.list_children(fid) if fid else []

    def fetch_calendar(self, version: int | None) -> tuple[int, bytes, str | None]:
        """Download the calendar .xlsx from 00_Calendar & Docs — the given version, or
        the latest if version is None. Returns (version, bytes, drive_view_link)."""
        if not self.docs:
            raise FileNotFoundError(f"{rules.SUBFOLDER_DOCS} not found on Drive.")
        best = None  # (version, file)
        for f in self.drive.list_children(self.docs):
            parsed = rules.parse_calendar_filename(f["name"])
            if parsed and parsed[0] == self.calendar_id:
                if version is not None and parsed[1] != version:
                    continue
                if best is None or parsed[1] > best[0]:
                    best = (parsed[1], f)
        if not best:
            want = f"v{version}" if version else "any version"
            raise FileNotFoundError(
                f"no {rules.CALENDAR_PREFIX}_{self.calendar_id}_{want}.xlsx in "
                f"{rules.SUBFOLDER_DOCS} on Drive.")
        return best[0], self.drive.download_bytes(best[1]["id"]), best[1].get("webViewLink")

    def assets_for(self, row_id: str) -> dict:
        pre = f"{row_id}_"
        for c in self.carousels:
            if c.get("mimeType") == FOLDER_MIME and c["name"].startswith(pre):
                slides = sorted((f for f in self.drive.list_children(c["id"])
                                 if f["name"].lower().endswith((".png", ".jpg", ".jpeg"))),
                                key=lambda f: f["name"])
                if slides:
                    return {"kind": "carousel",
                            "images": [_drive_ref(self.drive, s) for s in slides],
                            "video": None}
        imgs = [f for f in self.images if f["name"].startswith(pre)]
        if imgs:
            return {"kind": "image",
                    "images": [_drive_ref(self.drive, imgs[0])], "video": None}
        vids = [f for f in self.videos if f["name"].startswith(pre)]
        if vids:
            return {"kind": "video", "images": [],
                    "video": _drive_ref(self.drive, vids[0])}
        return {"kind": "none", "images": [], "video": None}


# --- small helpers ---------------------------------------------------------
def _esc(s) -> str:
    return html.escape(str(s or ""))


def _week_of(date_str: str) -> tuple[str, str]:
    """(sort_key, label) for the Monday-anchored week containing date_str."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return ("9999", "Unscheduled")
    monday = d - timedelta(days=d.weekday())
    return (monday.isoformat(), f"Week of {monday:%b} {monday.day}")


def _fmt_day(date_str: str, day: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d:%a} {d:%b} {d.day}"
    except ValueError:
        return day or date_str


SVG = {
    "heart": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/></svg>',
    "comment": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.5 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.7A8.5 8.5 0 1 1 21 11.5z"/></svg>',
    "share": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7z"/></svg>',
    "bookmark": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
    "play": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
    "film": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/></svg>',
    "like": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M7 10v11H4V10zM7 10l4-7a2 2 0 0 1 3 2l-1 5h5a2 2 0 0 1 2 2.3l-1.5 7A2 2 0 0 1 19 22H7"/></svg>',
    "globe": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18z"/></svg>',
    "warn": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 2 20h20L12 3z"/><path d="M12 9v5M12 17.5v.5"/></svg>',
    "stack": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 3h11a2 2 0 0 1 2 2v11h-2V5H8V3z"/><rect x="3" y="7" width="13" height="13" rx="2"/></svg>',
    "copy": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M6 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v2"/></svg>',
    "sheet": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg>',
    "ext": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M14 4h6v6M20 4l-9 9M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6"/></svg>',
    "reel": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 4h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm6 4v8l6-4-6-4z"/></svg>',
}


def _caption_block(handle: str, caption: str) -> str:
    if not caption:
        return ""
    return (f'<p class="cap"><span class="cap-user">{_esc(handle)}</span> '
            f'{_esc(caption)}</p>')


def _hashtags_block(tags: str, label: str) -> str:
    if not tags:
        return ""
    return f'<p class="tags"><span class="tags-lbl">{label}</span> {_esc(tags)}</p>'


# --- media rendering -------------------------------------------------------
def _media_html(assets: dict, link: str | None, is_vertical: bool, recorded: bool,
                failed: bool = False, reason: str = "", cache=None) -> str:
    if assets["kind"] == "carousel":
        slides = "".join(
            f'<img src="{_data_uri(ref, 520, cache)}" alt="slide {i+1}" loading="lazy">'
            for i, ref in enumerate(assets["images"]))
        dots = "".join('<span></span>' for _ in assets["images"])
        n = len(assets["images"])
        return (f'<div class="media carousel"><div class="track">{slides}</div>'
                f'<button class="cnav prev" aria-label="Previous slide">‹</button>'
                f'<button class="cnav next" aria-label="Next slide">›</button>'
                f'<span class="badge"><b class="cidx">1</b>/{n}</span>'
                f'<div class="dots">{dots}</div></div>')
    if assets["kind"] == "image":
        uri = _data_uri(assets["images"][0], 800, cache)
        return f'<div class="media"><img src="{uri}" alt="post image" loading="lazy"></div>'
    # status / poster tiles (recorded > failed > video > not-generated)
    if recorded:
        inner = (f'<div class="ph-icon">{SVG["film"]}</div>'
                 f'<div class="ph-label">Recorded — Wiah to camera</div>')
        return f'<div class="media poster">{inner}</div>'
    if failed:
        sub = f'<div class="ph-sub">{_esc(reason)}</div>' if reason else ""
        inner = (f'<div class="ph-icon warn">{SVG["warn"]}</div>'
                 f'<div class="ph-label">Generation failed</div>{sub}')
        return f'<div class="media poster fail">{inner}</div>'
    if assets["kind"] == "video":
        ar = "vert" if is_vertical else "wide"
        poster = _video_poster_uri(assets["video"], 720, cache) if assets.get("video") else None
        # the play button itself opens the clip on Drive (no separate text link)
        tag, attrs = ("a", f' href="{_esc(link)}" target="_blank" rel="noopener"') \
            if link else ("div", "")
        if poster:
            return (f'<div class="media vframe {ar}"><img src="{poster}" alt="video frame">'
                    f'<{tag} class="vplay" title="Open clip on Drive"{attrs}>'
                    f'{SVG["play"]}</{tag}></div>')
        inner = (f'<{tag} class="ph-icon play" title="Open clip on Drive"{attrs}>'
                 f'{SVG["play"]}</{tag}><div class="ph-label">Video preview</div>')
        return f'<div class="media poster {ar}">{inner}</div>'
    return '<div class="media poster"><div class="ph-label">Not generated yet</div></div>'


# --- per-platform cards ----------------------------------------------------
_AVATAR_URI: str | None = None  # brand photo, set once per build; None -> monogram


def _avatar(cls: str = "") -> str:
    # the photo is embedded once as a CSS background (.ava-photo), not per-card
    if _AVATAR_URI:
        return f'<span class="avatar ava-photo {cls}"></span>'
    return f'<span class="avatar {cls}">W</span>'


def _status_kind(status: str) -> str:
    """Spreadsheet Status -> color kind: Draft=yellow, Approved=green, else red."""
    s = (status or "").strip().lower()
    if s == "draft":
        return "draft"
    if s == "approved":
        return "ok"
    return "other"


def _status_pill(status: str) -> str:
    # color comes from the parent card's st-{kind} custom properties
    label = _esc(status.strip()) if status and status.strip() else "—"
    return f'<span class="pill">{label}</span>'


def _card(job, assets: dict, cache=None, sheet_link: str | None = None) -> str:
    key = _platform_key(job.platform)
    handle = _handle(job.platform, key)
    link = job.existing_link if isinstance(job.existing_link, str) \
        and job.existing_link.startswith("http") else None
    recorded = job.visual_type.strip().lower().startswith("recorded")
    # the generate workflow writes "Failed" into the asset-link cell on failure,
    # with the reason on the "[auto] ..." line in Notes.
    failed = isinstance(job.existing_link, str) \
        and job.existing_link.strip().lower() == "failed" \
        and assets["kind"] == "none"
    reason = ""
    if failed:
        for ln in job.notes.splitlines():
            if ln.strip().startswith("[auto]"):
                reason = ln.strip()[len("[auto]"):].strip()
                break
    if key == "facebook":
        body = (
            f'<div class="fb-head">{_avatar()}<div><div class="fb-name">'
            f'{_PLATFORMS["facebook"]["name"]}</div>'
            f'<div class="fb-sub">{_esc(_fmt_day(job.date, job.day))} · '
            f'<span class="ico xs">{SVG["globe"]}</span></div></div></div>'
            f'{_caption_block("", job.caption)}'
            f'{_media_html(assets, link, False, recorded, failed, reason, cache)}'
            f'<div class="fb-actions"><span class="ico">{SVG["like"]}</span>Like'
            f'<span class="ico">{SVG["comment"]}</span>Comment'
            f'<span class="ico">{SVG["share"]}</span>Share</div>')
    elif key == "tiktok":
        body = (
            f'<div class="tt-frame">{_media_html(assets, link, True, recorded, failed, reason, cache)}'
            f'<div class="tt-rail">{_avatar("sm")}'
            f'<span class="ico">{SVG["heart"]}</span><span class="ico">{SVG["comment"]}</span>'
            f'<span class="ico">{SVG["bookmark"]}</span><span class="ico">{SVG["share"]}</span></div>'
            f'<div class="tt-cap"><div class="tt-user">{_esc(handle)}</div>'
            f'<div class="tt-text">{_esc(job.caption)}</div></div></div>')
    else:  # instagram
        body = (
            f'<div class="ig-head">{_avatar("ring")}<span class="ig-user">{_esc(handle)}</span>'
            f'<span class="ig-more">···</span></div>'
            f'{_media_html(assets, link, False, recorded, failed, reason, cache)}'
            f'<div class="ig-actions"><span class="ico">{SVG["heart"]}</span>'
            f'<span class="ico">{SVG["comment"]}</span><span class="ico">{SVG["share"]}</span>'
            f'<span class="ico bm">{SVG["bookmark"]}</span></div>'
            f'{_caption_block(handle, job.caption)}'
            f'{_hashtags_block(job.hashtags, "First comment:")}')

    kind = _status_kind(job.status)
    asset_link = job.existing_link if isinstance(job.existing_link, str) \
        and job.existing_link.startswith("http") else None
    actions = []
    if job.caption:
        actions.append(f'<button class="act" data-copy="{_esc(job.caption)}">'
                       f'{SVG["copy"]} Caption</button>')
    if job.hashtags:
        actions.append(f'<button class="act" data-copy="{_esc(job.hashtags)}">'
                       f'{SVG["copy"]} Tags</button>')
    if sheet_link:
        actions.append(f'<a class="act" href="{_esc(sheet_link)}" target="_blank" '
                       f'rel="noopener">{SVG["sheet"]} Sheet</a>')
    if asset_link:
        actions.append(f'<a class="act" href="{_esc(asset_link)}" target="_blank" '
                       f'rel="noopener">{SVG["ext"]} Asset</a>')
    actions_html = f'<div class="card-actions">{"".join(actions)}</div>' if actions else ""
    head = (
        '<div class="card-head"><div class="chead-row">'
        f'<span class="rid">{_esc(job.row_id)}</span>'
        f'<span class="cdate">{_esc(_fmt_day(job.date, job.day))}</span>'
        + (f'<span class="cfmt">{_esc(job.fmt)}</span>' if job.fmt else "")
        + _status_pill(job.status) + '</div>'
        + (f'<div class="chook">{_esc(job.hook)}</div>' if job.hook else "")
        + actions_html + '</div>')
    return (f'<article class="card {key} st-{kind}" data-platform="{key}" '
            f'data-status="{kind}">{head}<div class="frame">{body}</div></article>')


def _grid_cell(job, assets: dict, cache=None) -> str:
    """One square tile in the Instagram profile grid."""
    recorded = job.visual_type.strip().lower().startswith("recorded")
    failed = isinstance(job.existing_link, str) \
        and job.existing_link.strip().lower() == "failed" and assets["kind"] == "none"
    corner = ""
    if assets["kind"] in ("image", "carousel"):
        uri = _data_uri(assets["images"][0], 340, cache)
        inner = f'<img src="{uri}" loading="lazy" alt="{_esc(job.row_id)}">'
        if assets["kind"] == "carousel":
            corner = f'<span class="gcorner">{SVG["stack"]}</span>'
    elif assets["kind"] == "video":
        poster = _video_poster_uri(assets["video"], 340, cache) if assets.get("video") else None
        inner = (f'<img src="{poster}" loading="lazy" alt="{_esc(job.row_id)}">'
                 if poster else f'<div class="gph">{SVG["play"]}</div>')
        corner = f'<span class="gcorner">{SVG["reel"]}</span>'
    elif recorded:
        inner = f'<div class="gph">{SVG["film"]}</div>'
        corner = f'<span class="gcorner">{SVG["reel"]}</span>'
    elif failed:
        inner = f'<div class="gph gph-fail">{SVG["warn"]}</div>'
    else:
        inner = '<div class="gph gph-none"></div>'
    kind = _status_kind(job.status)
    return (f'<div class="gcell st-{kind}" data-status="{kind}" '
            f'title="{_esc(job.row_id)} · {_esc(job.status)} · {_esc(job.hook)}">'
            f'{inner}{corner}</div>')


# --- page assembly ---------------------------------------------------------
def build_preview(calendar_id: str, version: int | None = None, *,
                  out_path: Path | None = None, quarter_folder: str | None = None,
                  no_cache: bool = False, emit=None) -> dict:
    """Build the HTML review page. Both the calendar workbook and every post asset are
    read from Google Drive (the shared, actively-edited copy) — ``version`` selects a
    specific draft, or the latest on Drive when omitted. Thumbnails are cached by Drive
    md5 so re-runs only re-fetch what changed."""
    import io
    import sys
    from .calendar import Calendar
    emit = emit or (lambda m, **k: print(m, file=sys.stderr))

    cache = None if no_cache else _ImgCache(
        config.generated_dir() / f".preview_cache_{calendar_id}.json")

    global _AVATAR_URI
    ap = config.brand_avatar_path()
    _AVATAR_URI = _encode(ap.read_bytes(), 220, quality=86) if ap.exists() else None
    avatar_css = (f'.ava-photo{{background-image:url({_AVATAR_URI})}}'
                  if _AVATAR_URI else "")

    # Calendar + assets both come from Google Drive.
    from ..core.drive import DriveClient
    client = DriveClient(config.credentials_path(), config.token_path(),
                         allow_interactive=False)
    drive_source = _DriveSource(client, calendar_id, quarter_folder)
    version, xlsx_bytes, sheet_link = drive_source.fetch_calendar(version)
    emit(f"calendar: {rules.calendar_filename(calendar_id, version)} (from Drive)")

    def resolve(row_id: str) -> dict:
        return drive_source.assets_for(row_id)

    cal = Calendar(io.BytesIO(xlsx_bytes))
    jobs = [j for j in cal.read_jobs() if j.row_id]
    jobs.sort(key=lambda j: (j.date or "9999", j.platform))

    # group by week; tally platforms and statuses
    weeks: dict[str, list] = {}
    labels: dict[str, str] = {}
    counts = {"instagram": 0, "facebook": 0, "tiktok": 0}
    scount = {"draft": 0, "ok": 0, "other": 0}
    n_asset = 0
    for j in jobs:
        key, label = _week_of(j.date)
        weeks.setdefault(key, []).append(j)
        labels[key] = label
        counts[_platform_key(j.platform)] = counts.get(_platform_key(j.platform), 0) + 1
        scount[_status_kind(j.status)] += 1

    sections = []
    grid_cells = []
    for wk in sorted(weeks):
        cards = []
        wposts = weeks[wk]
        approved = sum(1 for j in wposts if _status_kind(j.status) == "ok")
        pct = round(100 * approved / len(wposts)) if wposts else 0
        for j in wposts:
            assets = resolve(j.row_id)
            if assets["kind"] in ("image", "carousel"):
                n_asset += 1
            cards.append(_card(j, assets, cache, sheet_link))
            if _platform_key(j.platform) == "instagram":
                grid_cells.append(_grid_cell(j, assets, cache))
        rollup = (f'<span class="wk-prog"><span class="wk-count">{approved}/{len(wposts)} '
                  f'approved</span><span class="wk-bar"><i style="width:{pct}%"></i></span></span>')
        sections.append(f'<section class="week"><h2><span class="wk-label">'
                        f'{_esc(labels[wk])}</span><span class="wk-rule"></span>{rollup}</h2>'
                        f'<div class="grid">{"".join(cards)}</div></section>')

    emit(f"preview: {len(jobs)} posts, {scount['ok']} approved / {scount['draft']} draft "
         f"/ {scount['other']} other")
    chips = ('<div class="chips"><button class="chip active" data-f="all">All '
             f'<b>{len(jobs)}</b></button>'
             f'<button class="chip" data-f="instagram">Instagram <b>{counts["instagram"]}</b></button>'
             f'<button class="chip" data-f="facebook">Facebook <b>{counts["facebook"]}</b></button>'
             f'<button class="chip" data-f="tiktok">TikTok <b>{counts["tiktok"]}</b></button>'
             '<span class="chip-sep"></span>'
             f'<button class="chip" data-f="grid">▦ IG Grid <b>{counts["instagram"]}</b></button></div>')
    status_chips = (
        '<div class="chips status"><button class="chip active" data-s="all">All statuses '
        f'<b>{len(jobs)}</b></button>'
        f'<button class="chip st-draft" data-s="draft"><i class="sdot"></i>Draft '
        f'<b>{scount["draft"]}</b></button>'
        f'<button class="chip st-ok" data-s="ok"><i class="sdot"></i>Approved '
        f'<b>{scount["ok"]}</b></button>'
        f'<button class="chip st-other" data-s="other"><i class="sdot"></i>Other '
        f'<b>{scount["other"]}</b></button><span class="chip-sep"></span>'
        f'<button class="chip needs" data-s="needs">⚠ Needs review '
        f'<b>{scount["draft"] + scount["other"]}</b></button></div>')

    grid_html = (
        '<section id="grid" class="hide"><div class="profile">'
        f'{_avatar("ring lg")}<div class="pinfo">'
        '<div class="phandle">wiah_at_ghedeephilosophy</div>'
        f'<div class="pstats"><b>{counts["instagram"]}</b> posts &nbsp; '
        'Ghedee Philosophy</div>'
        '<div class="pbio">The 18 Universal Laws · a philosophy of living, with Wiah. '
        'Draft feed — newest first.</div></div></div>'
        f'<div class="iggrid">{"".join(reversed(grid_cells))}</div></section>')

    doc_title = f"Ghedee Social Calendar — {calendar_id.replace('_', ' ')} · Review v{version}"
    page = _PAGE.replace("{{TITLE}}", _esc(doc_title)).replace("{{CHIPS}}", chips) \
        .replace("{{STATUS_CHIPS}}", status_chips) \
        .replace("{{SECTIONS}}", "".join(sections)).replace("{{GRID}}", grid_html) \
        .replace("{{AVATAR_CSS}}", avatar_css) \
        .replace("{{SUBTITLE}}", f"{len(jobs)} posts · draft review")

    result = {"calendar_id": calendar_id, "version": version, "posts": len(jobs),
              "weeks": len(weeks), "with_images": n_asset}
    if cache is not None:
        cache.save()
        emit(f"cache: {cache.hits} reused, {cache.misses} re-encoded")
        result["cache"] = {"reused": cache.hits, "encoded": cache.misses}

    out_path = Path(out_path) if out_path else (
        rules.calendar_dir() / f"{rules.CALENDAR_PREFIX}_{calendar_id}_v{version}_preview.html")
    out_path.write_text(page, encoding="utf-8")
    emit(f"wrote {out_path}")
    result["path"] = str(out_path)
    return result


_PAGE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
:root{
  --ivory:#F4EFE1; --forest:#17281E; --gold:#C69A52; --terra:#B0524A; --sage:#93A084;
  --bg:#EFE9DA; --surface:#FBF8F0; --ink:#17281E; --muted:#6E7A6C; --line:#E2DAC7;
  --accent:#B08A3E;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#101B15; --surface:#182A20; --ink:#EDE7D7; --muted:#9AA79A; --line:#294034; --accent:#D2A85C;}}
:root[data-theme="dark"]{--bg:#101B15;--surface:#182A20;--ink:#EDE7D7;--muted:#9AA79A;--line:#294034;--accent:#D2A85C;}
:root[data-theme="light"]{--bg:#EFE9DA;--surface:#FBF8F0;--ink:#17281E;--muted:#6E7A6C;--line:#E2DAC7;--accent:#B08A3E;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:32px 20px 80px}
header.top{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;
  padding-bottom:18px;border-bottom:2px solid var(--gold);margin-bottom:8px}
header.top h1{font-family:Georgia,"Times New Roman",serif;font-weight:600;font-size:26px;
  letter-spacing:.2px;margin:0;text-wrap:balance}
header.top .sub{color:var(--muted);font-size:13px;text-transform:uppercase;letter-spacing:.12em}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 26px}
.chip{cursor:pointer;border:1px solid var(--line);background:var(--surface);color:var(--ink);
  border-radius:999px;padding:6px 14px;font-size:13px;font-weight:600;display:inline-flex;gap:7px;align-items:center}
.chip b{color:var(--muted);font-weight:600}
.chip.active{background:var(--forest);color:var(--ivory);border-color:var(--forest)}
.chip.active b{color:var(--gold)}
:root[data-theme="dark"] .chip.active,@media(prefers-color-scheme:dark){.chip.active{background:var(--gold);color:#17281E;border-color:var(--gold)}.chip.active b{color:#17281E}}
/* status filter chips (second row) */
.chips.status{margin:-14px 0 26px}
.chip .sdot{width:10px;height:10px;border-radius:3px;background:var(--sc-bright);display:inline-block}
.chip.st-draft.active,.chip.st-ok.active,.chip.st-other.active{
  background:var(--sc-bright);border-color:var(--sc-bright);color:var(--sc-ink)}
.chip.st-draft.active b,.chip.st-ok.active b,.chip.st-other.active b{color:var(--sc-ink);opacity:.75}
.chip.needs{border-color:#E3AE17;color:#9a6f10;font-weight:700}
.chip.needs.active{background:#F5C518;border-color:#F5C518;color:#4a3800}
.chip.needs.active b{color:#4a3800;opacity:.75}
/* per-card action buttons */
.card-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.act{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;
  color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:7px;
  padding:4px 9px;cursor:pointer;text-decoration:none;transition:background .12s,border-color .12s,color .12s}
.act:hover{border-color:var(--accent);color:var(--accent)}
.act svg{width:13px;height:13px;flex:none}
.act.copied{background:#1FC24C;border-color:#1FC24C;color:#fff}
.week{margin:30px 0}
.week h2{font-family:Georgia,serif;font-weight:600;font-size:17px;margin:0 0 16px;
  color:var(--ink);display:flex;align-items:center;gap:14px}
.wk-rule{flex:1;height:1px;background:var(--line);min-width:16px}
.wk-prog{display:inline-flex;align-items:center;gap:10px;
  font:600 12px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--muted)}
.wk-count{white-space:nowrap}
.wk-bar{width:110px;height:6px;border-radius:99px;background:var(--line);overflow:hidden}
.wk-bar i{display:block;height:100%;background:#1FC24C;border-radius:99px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:22px;align-items:start}
/* per-status color tokens (Draft=yellow, Approved=green, anything else=red) */
.st-draft{--sc:#E3AE17;--sc-bright:#F5C518;--sc-tint:rgba(245,197,24,.18);--sc-ink:#4a3800}
.st-ok{--sc:#1FA64A;--sc-bright:#1FC24C;--sc-tint:rgba(31,194,76,.15);--sc-ink:#fff}
.st-other{--sc:#DE2F22;--sc-bright:#F1362C;--sc-tint:rgba(241,54,44,.14);--sc-ink:#fff}
/* each post is a box framed in its status color, with a prominent header on top */
.card{display:flex;flex-direction:column;border:3px solid var(--sc);border-radius:14px;
  overflow:hidden;background:var(--surface);box-shadow:0 2px 12px rgba(20,30,22,.08)}
.card-head{padding:11px 14px 13px;background:var(--sc-tint);border-bottom:2px solid var(--sc)}
.chead-row{display:flex;align-items:center;gap:9px;margin-bottom:7px}
.chead-row .rid{font-weight:800;font-size:13px;color:var(--ink);letter-spacing:.02em}
.chead-row .cdate{font-size:12px;color:var(--muted);font-weight:600}
.chead-row .cfmt{font-size:10px;color:var(--muted);border:1px solid var(--line);
  padding:1px 7px;border-radius:999px;text-transform:uppercase;letter-spacing:.04em}
.chead-row .pill{margin-left:auto}
.chook{font-family:Georgia,serif;font-size:15.5px;line-height:1.32;color:var(--ink);
  font-weight:600;text-wrap:balance}
.pill{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;
  padding:4px 11px;border-radius:7px;background:var(--sc-bright);color:var(--sc-ink);
  box-shadow:0 1px 3px rgba(0,0,0,.22)}
.frame{overflow:hidden}
/* avatar monogram */
.avatar{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;
  overflow:hidden;background:radial-gradient(circle at 30% 25%,#2c4a38,#17281E);
  color:var(--ivory);font-family:Georgia,serif;font-size:16px;flex:none}
.avatar.ava-photo{background-size:cover;background-position:center;background-repeat:no-repeat}
{{AVATAR_CSS}}
.avatar.ring{box-shadow:0 0 0 2px #fff,0 0 0 4px var(--gold)}
.avatar.sm{width:44px;height:44px;font-size:19px;box-shadow:0 0 0 2px rgba(255,255,255,.9)}
.ico{display:inline-flex;width:24px;height:24px}
.ico svg{width:100%;height:100%}
.ico.xs{width:13px;height:13px;vertical-align:middle}
/* media */
.media{background:#0d0d0d;display:block}
.media img{display:block;width:100%;height:auto}
.media.carousel{position:relative}
.media.carousel .track{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;scrollbar-width:none}
.media.carousel .track::-webkit-scrollbar{display:none}
.media.carousel .track img{flex:0 0 100%;scroll-snap-align:center}
.media .badge{position:absolute;top:10px;right:10px;background:rgba(0,0,0,.6);color:#fff;
  font-size:11px;font-weight:600;padding:2px 9px;border-radius:999px}
.dots{position:absolute;bottom:10px;left:0;right:0;display:flex;justify-content:center;gap:5px}
.dots span{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.55);cursor:pointer}
.dots span.on{background:#fff;transform:scale(1.15)}
.cnav{position:absolute;top:44%;transform:translateY(-50%);width:30px;height:30px;border:none;
  border-radius:50%;background:rgba(20,30,22,.5);color:#fff;font-size:19px;line-height:1;
  cursor:pointer;display:grid;place-items:center;z-index:2;opacity:0;transition:opacity .15s}
.media.carousel:hover .cnav{opacity:1}
.cnav.prev{left:8px}.cnav.next{right:8px}
.cnav[disabled]{opacity:0!important;pointer-events:none}
.poster{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;
  color:#cfcfc7;background:linear-gradient(150deg,#1f3529,#17281E);min-height:220px;padding:26px}
.poster.vert{aspect-ratio:9/16}
.poster.wide{aspect-ratio:16/9}
.ph-icon{width:52px;height:52px;color:var(--gold);opacity:.9}
.ph-icon.play{display:grid;place-items:center;background:rgba(255,255,255,.12);border-radius:50%;padding:12px}
.ph-icon.warn{width:44px;height:44px;color:#f0c9c4}
.ph-label{color:#e8e2d4;font-size:13px;font-weight:600;text-align:center}
.ph-sub{color:#e6c7c2;font-size:11.5px;text-align:center;max-width:88%;line-height:1.35}
.ph-link{color:var(--gold);font-size:12.5px;text-decoration:none;border-bottom:1px solid rgba(198,154,82,.5)}
.poster.fail{background:linear-gradient(150deg,#5a2620,#3a1a17)}
/* video first-frame poster */
.media.vframe{position:relative;background:#000}
.media.vframe.wide{aspect-ratio:16/9}.media.vframe.vert{aspect-ratio:9/16}
.media.vframe img{width:100%;height:100%;object-fit:cover;display:block;filter:brightness(.82)}
.media.vframe .vplay{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  width:56px;height:56px;display:grid;place-items:center;color:#fff;
  background:rgba(20,30,22,.5);border-radius:50%;backdrop-filter:blur(2px);
  text-decoration:none;cursor:pointer;transition:background .15s,transform .15s}
.media.vframe a.vplay:hover{background:rgba(198,154,82,.85);
  transform:translate(-50%,-50%) scale(1.06)}
.media.vframe .vplay svg{width:26px;height:26px;margin-left:3px}
a.ph-icon.play{text-decoration:none;cursor:pointer}
/* instagram */
.card.instagram .frame{background:#fff;color:#0e0e0e}
.ig-head{display:flex;align-items:center;gap:10px;padding:10px 12px}
.ig-user{font-weight:600;font-size:14px}
.ig-more{margin-left:auto;color:#333;letter-spacing:1px}
.ig-actions{display:flex;align-items:center;gap:14px;padding:10px 12px 4px;color:#111}
.ig-actions .bm{margin-left:auto}
.cap{margin:2px 12px 10px;font-size:13.5px;line-height:1.45;color:#0e0e0e}
.cap-user{font-weight:600;margin-right:5px}
.tags{margin:0 12px 12px;font-size:12.5px;color:#3a5aa0}
.tags-lbl{color:#8a8a8a;font-weight:600;margin-right:4px}
/* facebook */
.card.facebook .frame{background:#fff;color:#0e0e0e}
.fb-head{display:flex;align-items:center;gap:10px;padding:12px 12px 8px}
.fb-name{font-weight:700;font-size:14px}
.fb-sub{font-size:12px;color:#65676b}
.fb-head .avatar{border-radius:8px}
.card.facebook .cap{margin:0 12px 10px}
.fb-actions{display:flex;align-items:center;justify-content:space-around;gap:8px;
  padding:8px 4px;margin-top:2px;border-top:1px solid #e4e6eb;color:#65676b;font-size:13px;font-weight:600}
.fb-actions .ico{width:19px;height:19px}
/* tiktok */
.card.tiktok .frame{background:#000}
.tt-frame{position:relative}
.tt-frame .media,.tt-frame .poster{aspect-ratio:9/16;min-height:0;width:100%}
.tt-frame .media img{height:100%;object-fit:cover}
.tt-rail{position:absolute;right:8px;bottom:78px;display:flex;flex-direction:column;
  align-items:center;gap:16px;color:#fff}
.tt-rail .ico{width:27px;height:27px;filter:drop-shadow(0 1px 2px rgba(0,0,0,.5))}
.tt-cap{position:absolute;left:12px;right:56px;bottom:12px;color:#fff;
  text-shadow:0 1px 3px rgba(0,0,0,.6)}
.tt-user{font-weight:700;font-size:14px;margin-bottom:3px}
.tt-text{font-size:12.5px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.hide{display:none!important}
.chip-sep{width:1px;align-self:stretch;background:var(--line);margin:2px 2px}
/* instagram profile grid */
#grid .profile{display:flex;align-items:center;gap:26px;max-width:820px;margin:6px auto 26px;
  padding:0 6px}
.avatar.lg{width:82px;height:82px;font-size:38px}
#grid .pinfo{min-width:0}
#grid .phandle{font-size:19px;font-weight:600;margin-bottom:8px}
#grid .pstats{font-size:14px;color:var(--muted);margin-bottom:8px}
#grid .pstats b{color:var(--ink)}
#grid .pbio{font-size:13.5px;color:var(--ink);line-height:1.5;max-width:52ch}
.iggrid{max-width:820px;margin:0 auto;display:grid;grid-template-columns:repeat(3,1fr);gap:3px}
.gcell{position:relative;aspect-ratio:1;overflow:hidden;background:#0d0d0d}
/* status frame around each grid item (Draft=yellow, Approved=green, else red),
   drawn as an overlay so it sits ON TOP of the thumbnail */
.gcell::after{content:"";position:absolute;inset:0;pointer-events:none;
  border:4px solid var(--sc-bright,transparent);z-index:3}
.gcell img{width:100%;height:100%;object-fit:cover;display:block}
.gcorner{position:absolute;top:6px;right:6px;width:19px;height:19px;color:#fff;
  filter:drop-shadow(0 1px 2px rgba(0,0,0,.5))}
.gph{width:100%;height:100%;display:grid;place-items:center;
  background:linear-gradient(150deg,#22382b,#17281E);color:var(--gold)}
.gph svg{width:34px;height:34px}
.gph-fail{background:linear-gradient(150deg,#5a2620,#3a1a17);color:#f0c9c4}
.gph-none{background:repeating-linear-gradient(45deg,#20302a,#20302a 8px,#1a2823 8px,#1a2823 16px)}
@media(max-width:520px){#grid .profile{gap:16px}.avatar.lg{width:60px;height:60px;font-size:28px}}
footer{margin-top:40px;color:var(--muted);font-size:12px;text-align:center}
</style>
<div class="wrap">
  <header class="top"><h1>Ghedee Social Calendar</h1><span class="sub">{{SUBTITLE}}</span></header>
  {{CHIPS}}
  {{STATUS_CHIPS}}
  <div id="feed">{{SECTIONS}}</div>
  {{GRID}}
  <footer>Draft review · nothing here is published. Approve in the calendar, not this page.</footer>
</div>
<script>
document.querySelectorAll('.media.carousel').forEach(function(c){
  var track=c.querySelector('.track'), dots=c.querySelectorAll('.dots span'),
      idxEl=c.querySelector('.cidx'), prev=c.querySelector('.prev'),
      next=c.querySelector('.next'), n=track.children.length, i=0;
  function sync(){ dots.forEach(function(d,j){d.classList.toggle('on',j===i)});
    if(idxEl) idxEl.textContent=i+1;
    if(prev) prev.disabled=i===0; if(next) next.disabled=i===n-1; }
  function go(k){ i=Math.max(0,Math.min(n-1,k));
    track.scrollTo({left:i*track.clientWidth,behavior:'smooth'}); sync(); }
  if(prev) prev.addEventListener('click',function(e){e.stopPropagation();go(i-1)});
  if(next) next.addEventListener('click',function(e){e.stopPropagation();go(i+1)});
  dots.forEach(function(d,j){d.addEventListener('click',function(){go(j)})});
  var t; track.addEventListener('scroll',function(){clearTimeout(t);t=setTimeout(function(){
    var k=Math.round(track.scrollLeft/Math.max(1,track.clientWidth));
    if(k!==i){i=k; sync();} },90)});
  sync();
});
var flt={f:'all', s:'all'};
function statusMatch(s){
  if(flt.s==='all') return true;
  if(flt.s==='needs') return s!=='ok';
  return s===flt.s;
}
function applyFilter(){
  var grid=document.getElementById('grid'), feed=document.getElementById('feed');
  var inGrid = flt.f==='grid';
  feed.classList.toggle('hide', inGrid);
  if(grid) grid.classList.toggle('hide', !inGrid);
  if(inGrid){
    document.querySelectorAll('.gcell').forEach(function(c){
      c.classList.toggle('hide', !statusMatch(c.dataset.status)); });
    return;
  }
  document.querySelectorAll('.card').forEach(function(c){
    var vis=(flt.f==='all'||c.dataset.platform===flt.f) && statusMatch(c.dataset.status);
    c.classList.toggle('hide', !vis);
  });
  document.querySelectorAll('.week').forEach(function(w){
    w.classList.toggle('hide', !w.querySelector('.card:not(.hide)')); });
}
document.querySelectorAll('.chip[data-f]').forEach(function(btn){
  btn.addEventListener('click',function(){
    document.querySelectorAll('.chip[data-f]').forEach(function(b){b.classList.remove('active')});
    btn.classList.add('active'); flt.f=btn.dataset.f; applyFilter();
  });
});
document.querySelectorAll('.chip[data-s]').forEach(function(btn){
  btn.addEventListener('click',function(){
    document.querySelectorAll('.chip[data-s]').forEach(function(b){b.classList.remove('active')});
    btn.classList.add('active'); flt.s=btn.dataset.s; applyFilter();
  });
});
// copy caption / hashtags to clipboard (with a file:// fallback)
function copyText(t){
  if(navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(t);
  return new Promise(function(res){
    var ta=document.createElement('textarea'); ta.value=t; ta.style.position='fixed';
    ta.style.opacity='0'; document.body.appendChild(ta); ta.select();
    try{document.execCommand('copy')}catch(e){} document.body.removeChild(ta); res();
  });
}
document.querySelectorAll('.act[data-copy]').forEach(function(b){
  b.addEventListener('click',function(){
    copyText(b.dataset.copy).then(function(){
      var html=b.innerHTML; b.classList.add('copied'); b.innerHTML='Copied ✓';
      setTimeout(function(){b.classList.remove('copied'); b.innerHTML=html;},1200);
    });
  });
});
</script>
"""
