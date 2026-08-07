"""social.preview — a self-contained HTML review page of the calendar's posts.

Renders every scheduled post as a mockup in its platform's real chrome (Instagram
feed card, Facebook post, TikTok 9:16 with the action rail), grouped by week, so a
reviewer can approve the round in context. Each row's asset is located SOLELY from
the sheet's Generated Asset Link column — the single source of truth — then read from
Google Drive, downscaled, JPEG-compressed, and inlined as a data URI, so the page is
a single portable file (no external assets) — safe to open locally, share, or upload
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
from ..core.drive import FOLDER_MIME, GSHEET_MIME, file_id_from_link

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


def _uri_dims(uri: str | None) -> tuple[int, int] | None:
    """Pixel size of an encoded data: URI, read from the JPEG header only (PIL opens
    lazily, so no full decode). Used to emit width/height on the <img>: without them a
    slide has zero height until it decodes, and a carousel that reflows mid-load makes
    Safari re-snap to a later slide. Returns None if it can't be determined."""
    if not uri or "," not in uri:
        return None
    try:
        from PIL import Image
        return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).size
    except Exception:
        return None


def _dim_attrs(uri: str | None) -> str:
    wh = _uri_dims(uri)
    return f' width="{wh[0]}" height="{wh[1]}"' if wh else ""


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


_NONE_ASSET = {"kind": "none", "images": [], "video": None}


def _linked_asset(drive, job) -> dict:
    """Resolve a row's asset SOLELY from its Generated Asset Link column — the sheet is
    the single source of truth (what generate wrote, whether the model produced it or it
    was copied from a Selected Asset). An image/video link renders as that one file; a
    carousel's link is a group folder, listed for its slides. A blank / 'Failed' cell, a
    non-Drive link, or a link that no longer resolves yields kind 'none' (the card shows
    the appropriate placeholder). No Drive folder scanning / prefix matching is done."""
    link = job.existing_link
    if not (isinstance(link, str) and link.startswith("http")):
        return _NONE_ASSET
    fid = file_id_from_link(link)
    if not fid:
        return _NONE_ASSET

    if job.plan.kind == "carousel":
        try:
            slides = sorted(
                (f for f in drive.list_children(fid)
                 if f["name"].lower().endswith((".png", ".jpg", ".jpeg"))),
                key=lambda f: f["name"])
        except Exception:
            return _NONE_ASSET
        if not slides:
            return _NONE_ASSET
        return {"kind": "carousel",
                "images": [_drive_ref(drive, s) for s in slides], "video": None}

    try:
        meta = drive.get_file(fid)
    except Exception:
        return _NONE_ASSET
    if meta.get("mimeType") == FOLDER_MIME:
        return _NONE_ASSET
    ref = _drive_ref(drive, meta)  # md5/modifiedTime keeps the thumbnail cache correct
    if job.plan.kind == "video":
        return {"kind": "video", "images": [], "video": ref}
    return {"kind": "image", "images": [ref], "video": None}


# --- calendar source -------------------------------------------------------
class _DriveSource:
    """Locates the calendar spreadsheet on Drive (the 00_Calendar & Docs folder) and
    reads it as .xlsx. Post assets are NOT scanned here — they're resolved per row from
    the sheet's Generated Asset Link column (see _linked_asset), which is the single
    source of truth."""

    def __init__(self, drive, calendar_id: str):
        self.drive = drive
        root = rules.social_calendar_root_id()
        if not root:
            raise RuntimeError("SOCIAL_CALENDAR_ROOT_ID is not set.")
        folder = rules.calendar_folder(calendar_id)
        self.calendar_id = calendar_id
        base = drive.find_folder_path(root, [folder])
        if not base:
            raise FileNotFoundError(f"Drive folder {folder!r} not found under the "
                                    "Social Calendar root.")
        self.docs = drive.find_folder_path(base, [rules.SUBFOLDER_DOCS])

    def fetch_calendar(self, version: int | None) -> tuple[str, bytes, str | None, str | None]:
        """Get the calendar as .xlsx bytes. With no version, prefer the LIVING Google
        Sheet (exported to .xlsx) so the preview reflects current edits; otherwise fall
        back to a versioned .xlsx snapshot. Returns (label, bytes, drive_view_link,
        spreadsheet_id) — spreadsheet_id is set only for the editable LIVING sheet (a
        Google Sheet's Drive id is its spreadsheetId); it is None for a frozen snapshot."""
        if not self.docs:
            raise FileNotFoundError(f"{rules.SUBFOLDER_DOCS} not found on Drive.")
        if version is None:
            live_name = f"{rules.CALENDAR_PREFIX}_{self.calendar_id}"
            live = self.drive.find_by_name(live_name, self.docs, mime=GSHEET_MIME)
            if live:
                return ("live", self.drive.export_as_xlsx(live["id"]),
                        live.get("webViewLink"), live["id"])
        best = None  # (version, file)
        for f in self.drive.list_children(self.docs):
            parsed = rules.parse_calendar_filename(f["name"])
            if parsed and parsed[0] == self.calendar_id:
                if version is not None and parsed[1] != version:
                    continue
                if best is None or parsed[1] > best[0]:
                    best = (parsed[1], f)
        if not best:
            want = f"v{version}" if version else "the live sheet or any .xlsx"
            raise FileNotFoundError(
                f"could not find {want} for {self.calendar_id} in "
                f"{rules.SUBFOLDER_DOCS} on Drive.")
        return (f"v{best[0]}", self.drive.download_bytes(best[1]["id"]),
                best[1].get("webViewLink"), None)


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


# --- simulated engagement --------------------------------------------------
# Mock like/view counts for the platform simulators. These are INVENTED — the calendar
# holds no performance data — so the only thing that matters is that they read plausibly
# and never move: a post showing 84 likes on one build and 231 on the next reads as a bug.
# Hence FNV-1a rather than hash(), which is salted per process and would differ every run.
_ENGAGE_LIKES = {           # primary metric band for a still post, per platform
    "instagram": (38, 260),  # an EMERGING account (the scale chosen at Functional Design);
    "facebook": (12, 90),    # edit these two tables to re-scale the whole simulator
    "tiktok": (25, 180),
}
_ENGAGE_VIEWS = (800, 14000)  # primary metric band for a Reel / TikTok clip


def _fnv1a32(s: str) -> int:
    """FNV-1a, 32-bit. Stable across processes, versions and platforms (unlike hash())."""
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def _band(row_id: str, metric: str, lo: int, hi: int) -> int:
    """A stable value in [lo, hi] for one (row, metric) pair. Salting the hash per metric
    keeps the draws independent, so likes and comments don't move in lockstep."""
    if hi <= lo:
        return lo
    return lo + _fnv1a32(f"{row_id}:{metric}") % (hi - lo + 1)


def engagement(row_id: str, platform: str, is_reel: bool = False) -> dict:
    """Plausible, deterministic mock engagement for one post.

    Pure and total. Secondary metrics are derived FROM the primary draw rather than drawn
    independently, which is what keeps the numbers coherent: comments can never exceed
    likes, and likes can never exceed views. An incoherent mockup looks broken.
    """
    rid = row_id or ""
    key = _platform_key(platform)
    out: dict = {}
    if is_reel or key == "tiktok":
        views = _band(rid, "views", *_ENGAGE_VIEWS)
        likes = max(1, views * _band(rid, "lr", 40, 90) // 1000)      # 4–9% of views
        out["views"] = views
    else:
        likes = _band(rid, "likes", *_ENGAGE_LIKES.get(key, _ENGAGE_LIKES["instagram"]))
    out["likes"] = likes
    out["comments"] = likes * _band(rid, "cr", 20, 80) // 1000        # 2–8% of likes
    out["shares"] = likes * _band(rid, "sr", 10, 40) // 1000          # 1–4%
    out["saves"] = likes * _band(rid, "vr", 30, 100) // 1000          # 3–10%
    return out


def _rel_time(date_str: str, newest: str) -> str:
    """"2h" / "3d" / "1w" — this post's age relative to the LATEST post in the calendar, so
    the top of a simulated feed reads as fresh and older posts age plausibly down it."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        n = datetime.strptime(newest, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return ""
    days = (n - d).days
    if days <= 0:
        return f"{2 + _fnv1a32(date_str) % 9}h"   # same day as the newest post
    if days < 7:
        return f"{days}d"
    if days < 35:
        return f"{days // 7}w"
    return f"{max(1, days // 30)}mo"


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
    # --- simulator chrome ---
    "phone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M11 18.5h2"/></svg>',
    "home": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/></svg>',
    "search": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    "add": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="18" height="18" rx="5"/><path d="M12 8v8M8 12h8"/></svg>',
    "person": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/></svg>',
    "music": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M9 18V6l10-2v12"/><circle cx="7" cy="18" r="2.5"/><circle cx="17" cy="16" r="2.5"/></svg>',
    "inbox": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 12h5l2 3h4l2-3h5"/><path d="M5 5h14l2 7v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-6z"/></svg>',
}


def _caption_block(handle: str, caption: str) -> str:
    if not caption:
        return ""
    if not handle:  # e.g. Facebook, where the name is in the post header, not inline
        return f'<p class="cap">{_esc(caption)}</p>'
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
        # Slides carry width/height (and are NOT lazy): a lazy slide has no box until it
        # decodes, and each late reflow re-triggers Safari's mandatory snap, which lands
        # the carousel on slide 3 or 4 instead of the first. The bytes are already inline,
        # so lazy bought no network anyway.
        uris = [_data_uri(ref, 520, cache) for ref in assets["images"]]
        slides = "".join(
            f'<img src="{uri}" alt="slide {i+1}"{_dim_attrs(uri)}>'
            for i, uri in enumerate(uris))
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
    # A resolved clip (AI hero video OR one of Wiah's recorded clips) shows its poster
    # frame + play button, whatever the Visual Type — the linked asset wins.
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
    # no resolved asset -> a status / placeholder tile (failed > recorded > not-generated)
    if failed:
        sub = f'<div class="ph-sub">{_esc(reason)}</div>' if reason else ""
        inner = (f'<div class="ph-icon warn">{SVG["warn"]}</div>'
                 f'<div class="ph-label">Generation failed</div>{sub}')
        return f'<div class="media poster fail">{inner}</div>'
    if recorded:
        inner = (f'<div class="ph-icon">{SVG["film"]}</div>'
                 f'<div class="ph-label">Recorded — Wiah to camera</div>')
        return f'<div class="media poster">{inner}</div>'
    return '<div class="media poster"><div class="ph-label">Not generated yet</div></div>'


# --- per-platform cards ----------------------------------------------------
_AVATAR_URI: str | None = None  # brand photo, set once per build; None -> monogram


def _avatar(cls: str = "") -> str:
    # the photo is embedded once as a CSS background (.ava-photo), not per-card
    if _AVATAR_URI:
        return f'<span class="avatar ava-photo {cls}"></span>'
    return f'<span class="avatar {cls}">W</span>'


def _status_kind(status: str) -> str:
    """Spreadsheet Status -> color kind: Draft=yellow, Approved=green,
    Awaiting Asset=gray, Wiah Review=purple, else red."""
    s = (status or "").strip().lower()
    if s == "draft":
        return "draft"
    if s == "approved":
        return "ok"
    if s == "awaiting asset":
        return "await"
    if s == "wiah review":
        return "review"
    return "other"


def _is_reel(job) -> bool:
    """A reel = a post the team formats as 'Reel' (the vertical short-form clips —
    the recorded-Wiah reels on TikTok / IG Reels). Keyed on the Format column, the
    sheet's own categorization; the 16:9 AI hero videos are feed posts, not reels."""
    return (job.fmt or "").strip().lower() == "reel"


def _is_carousel(job) -> bool:
    """A carousel = Format 'Carousel' exactly (multi-slide), matching rules.plan_visual —
    the mixed 'Single image / carousel' string is a single feed image, not a carousel."""
    return (job.fmt or "").strip().lower() == "carousel"


# The statuses a reviewer can set from the preview's dropdown. Kept in sync with
# _status_kind's colour buckets; any other free-text status stays valid (it's shown
# as an extra option so editing never silently drops an unrecognised value).
STATUS_OPTIONS = ["Draft", "Awaiting Asset", "Wiah Review", "Approved"]


def _status_pill(status: str, row_id: str = "") -> str:
    """A native <select> styled as the status pill. Its colour comes from the parent
    card's st-{kind} custom properties. It renders disabled (looks like a static pill)
    unless the page is served via the Apps Script web app, where the script enables it
    and each change writes back to the living Sheet."""
    cur = status.strip() if status and status.strip() else ""
    opts = list(STATUS_OPTIONS)
    if cur and cur not in opts:
        opts.insert(0, cur)  # preserve an unrecognised status as a selectable option
    options = ""
    if not cur:
        options += '<option value="" selected>—</option>'
    options += "".join(
        f'<option{" selected" if o == cur else ""}>{_esc(o)}</option>' for o in opts)
    return (f'<select class="pill pill-edit" data-rowid="{_esc(row_id)}" '
            f'data-status="{_esc(cur)}" disabled '
            f'aria-label="Post status">{options}</select>')


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
        + _status_pill(job.status, job.row_id) + '</div>'
        + (f'<div class="chook">{_esc(job.hook)}</div>' if job.hook else "")
        + actions_html + '</div>')
    # data-rowid lets the simulator locate this card's media node and CLONE it, so every
    # asset's data URI is emitted into the page exactly once (see the simulator JS).
    return (f'<article class="card {key} st-{kind}" data-platform="{key}" '
            f'data-rowid="{_esc(job.row_id)}" '
            f'data-status="{kind}" data-reel="{1 if _is_reel(job) else 0}" '
            f'data-carousel="{1 if _is_carousel(job) else 0}">'
            f'{head}<div class="frame">{body}</div></article>')


# NOTE: the former `_grid_cell` (the standalone IG Grid's square tile) was removed with the
# grid view itself. Its replacement — the Instagram simulator's Profile tab — builds each tile
# in the browser by cloning the review card's <img>, so the page no longer carries a SECOND,
# 340px encoding of every Instagram asset. That deletion is what pays for the simulators.


# --- page assembly ---------------------------------------------------------
def build_preview(calendar_id: str, version: int | None = None, *,
                  out_path: Path | None = None,
                  no_cache: bool = False, publish: bool = True, emit=None) -> dict:
    """Build the HTML review page from Google Drive. With no ``version`` it reads the
    LIVING Google Sheet (current edits); a version reads that .xlsx snapshot instead.
    Thumbnails are cached by Drive md5. Unless ``publish`` is False, the finished page is
    also uploaded to 00_Calendar & Docs as Ghedee_Social_Calendar_<id>_preview.html."""
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
    drive_source = _DriveSource(client, calendar_id)
    label, xlsx_bytes, sheet_link, sheet_id = drive_source.fetch_calendar(version)
    emit(f"calendar: {calendar_id} ({label}) from Drive")

    def resolve(job) -> dict:
        # The sheet's Generated Asset Link column is the single source of truth for what
        # a row displays (covers model-generated and Selected-Asset-copied assets alike).
        return _linked_asset(client, job)

    cal = Calendar(io.BytesIO(xlsx_bytes))
    jobs = [j for j in cal.read_jobs() if j.row_id]
    jobs.sort(key=lambda j: (j.date or "9999", j.platform))

    # group by week; tally platforms and statuses
    weeks: dict[str, list] = {}
    labels: dict[str, str] = {}
    counts = {"instagram": 0, "facebook": 0, "tiktok": 0}
    scount = {"draft": 0, "ok": 0, "await": 0, "review": 0, "other": 0}
    n_asset = 0
    for j in jobs:
        # NB: distinct names — these used to be `key, label`, which clobbered the source
        # `label` from fetch_calendar, so the page title and the returned "source" field
        # reported a week heading ("Week of Nov 16") instead of "live" / "v3".
        wk_key, wk_label = _week_of(j.date)
        weeks.setdefault(wk_key, []).append(j)
        labels[wk_key] = wk_label
        counts[_platform_key(j.platform)] = counts.get(_platform_key(j.platform), 0) + 1
        scount[_status_kind(j.status)] += 1

    # newest scheduled date — the simulators' relative timestamps ("2h", "3d") are measured
    # from it, so the top of a simulated feed always reads as the freshest post.
    newest = max((j.date for j in jobs if j.date), default="")

    sections = []
    sim_posts = []
    for wk in sorted(weeks):
        cards = []
        wposts = weeks[wk]
        approved = sum(1 for j in wposts if _status_kind(j.status) == "ok")
        pct = round(100 * approved / len(wposts)) if wposts else 0
        for j in wposts:
            assets = resolve(j)
            if assets["kind"] in ("image", "carousel"):
                n_asset += 1
            cards.append(_card(j, assets, cache, sheet_link))
            # Simulator metadata ONLY — no image bytes. The simulator clones each card's
            # media node at runtime, so assets stay in the file exactly once.
            pkey = _platform_key(j.platform)
            link = j.existing_link if isinstance(j.existing_link, str) \
                and j.existing_link.startswith("http") else ""
            reel = _is_reel(j)
            sim_posts.append({
                "id": j.row_id, "p": pkey, "date": j.date or "",
                "day": _fmt_day(j.date, j.day), "st": _status_kind(j.status),
                "fmt": j.fmt or "", "reel": reel, "car": _is_carousel(j),
                "handle": _handle(j.platform, pkey), "cap": j.caption or "",
                "tags": j.hashtags or "", "hook": j.hook or "",
                "kind": assets["kind"],
                "eng": engagement(j.row_id, j.platform, reel),
                "rel": _rel_time(j.date, newest),
            })
        rollup = (f'<span class="wk-prog"><span class="wk-count">{approved}/{len(wposts)} '
                  f'approved</span><span class="wk-bar"><i style="width:{pct}%"></i></span></span>')
        sections.append(f'<section class="week"><h2><span class="wk-label">'
                        f'{_esc(labels[wk])}</span><span class="wk-rule"></span>{rollup}</h2>'
                        f'<div class="grid">{"".join(cards)}</div></section>')

    emit(f"preview: {len(jobs)} posts, {scount['ok']} approved / {scount['draft']} draft "
         f"/ {scount['await']} awaiting asset / {scount['review']} wiah review "
         f"/ {scount['other']} other")
    rcount = sum(1 for j in jobs if _is_reel(j))
    ccount = sum(1 for j in jobs if _is_carousel(j))
    chips = ('<div class="chips"><button class="chip active" data-f="all">All '
             f'<b>{len(jobs)}</b></button>'
             f'<button class="chip" data-f="instagram">Instagram <b>{counts["instagram"]}</b></button>'
             f'<button class="chip" data-f="reel">{SVG["reel"]} Reels <b>{rcount}</b></button>'
             f'<button class="chip" data-f="carousel">{SVG["stack"]} Carousels <b>{ccount}</b></button>'
             f'<button class="chip" data-f="facebook">Facebook <b>{counts["facebook"]}</b></button>'
             f'<button class="chip" data-f="tiktok">TikTok <b>{counts["tiktok"]}</b></button>'
             '<span class="chip-sep"></span>'
             f'<button class="chip sim-open" id="sim-open" data-testid="sim-open-chip">'
             f'{SVG["phone"]} Simulator</button></div>')
    status_chips = (
        '<div class="chips status"><button class="chip active" data-s="all">All statuses '
        f'<b>{len(jobs)}</b></button>'
        f'<button class="chip st-draft" data-s="draft"><i class="sdot"></i>Draft '
        f'<b>{scount["draft"]}</b></button>'
        f'<button class="chip st-ok" data-s="ok"><i class="sdot"></i>Approved '
        f'<b>{scount["ok"]}</b></button>'
        f'<button class="chip st-other" data-s="other"><i class="sdot"></i>Other '
        f'<b>{scount["other"]}</b></button>'
        f'<button class="chip st-await" data-s="await"><i class="sdot"></i>Awaiting Asset '
        f'<b>{scount["await"]}</b></button>'
        f'<button class="chip st-review" data-s="review"><i class="sdot"></i>Wiah Review '
        f'<b>{scount["review"]}</b></button><span class="chip-sep"></span>'
        f'<button class="chip delivered" data-s="delivered">✓ Asset Delivered '
        f'<b>{len(jobs) - scount["await"]}</b></button>'
        f'<button class="chip needs" data-s="needs">⚠ Needs review '
        f'<b>{scount["draft"] + scount["other"]}</b></button></div>')

    # Simulator feed order (FR-4): newest first, undated last, Row ID as a stable tie-break.
    sim_posts.sort(key=lambda s: (s["date"] or "0000", s["id"]), reverse=True)
    sim_posts.sort(key=lambda s: 1 if not s["date"] else 0)
    # "</script>" inside a caption would end the script block early; escaping "<" prevents it.
    sim_json = json.dumps(sim_posts, ensure_ascii=False).replace("<", "\\u003c")

    doc_title = f"Ghedee Social Calendar — {calendar_id.replace('_', ' ')} · Review ({label})"
    page = _PAGE.replace("{{TITLE}}", _esc(doc_title)).replace("{{CHIPS}}", chips) \
        .replace("{{STATUS_CHIPS}}", status_chips) \
        .replace("{{SECTIONS}}", "".join(sections)) \
        .replace("{{AVATAR_CSS}}", avatar_css) \
        .replace("{{SHEET_ID}}", _esc(sheet_id or "")) \
        .replace("{{SIM_POSTS}}", sim_json) \
        .replace("{{ICONS}}", json.dumps(SVG).replace("<", "\\u003c")) \
        .replace("{{SUBTITLE}}", f"{len(jobs)} posts · draft review")

    result = {"calendar_id": calendar_id, "source": label, "posts": len(jobs),
              "weeks": len(weeks), "with_images": n_asset}
    if cache is not None:
        cache.save()
        emit(f"cache: {cache.hits} reused, {cache.misses} re-encoded")
        result["cache"] = {"reused": cache.hits, "encoded": cache.misses}

    fname = f"{rules.CALENDAR_PREFIX}_{calendar_id}_preview.html"
    out_path = Path(out_path) if out_path else (rules.calendar_dir() / fname)
    out_path.write_text(page, encoding="utf-8")
    emit(f"wrote {out_path}")
    result["path"] = str(out_path)

    # publish alongside the calendar in 00_Calendar & Docs (same-named file, in place)
    if publish and drive_source.docs:
        up = drive_source.drive.upload(out_path, drive_source.docs)
        drive_source.drive.make_shareable(up["id"])
        result["drive_file"] = up["name"]
        result["drive_link"] = up["link"]
        emit(f"published preview -> {up['name']} in {rules.SUBFOLDER_DOCS}")

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
header.top .who{margin-left:auto;font-size:12px;color:var(--muted);border:1px solid var(--line);
  border-radius:999px;padding:4px 12px;display:inline-flex;align-items:center;gap:6px;
  white-space:nowrap;align-self:center}
header.top .who b{color:var(--ink);font-weight:600}
header.top .who a{color:var(--accent);font-weight:600;text-decoration:underline;white-space:nowrap}
header.top .who.warn{color:var(--terra);border-color:var(--terra)}
header.top .who.warn b{color:var(--terra)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 26px}
.chip{cursor:pointer;border:1px solid var(--line);background:var(--surface);color:var(--ink);
  border-radius:999px;padding:6px 14px;font-size:13px;font-weight:600;display:inline-flex;gap:7px;align-items:center}
.chip b{color:var(--muted);font-weight:600}
.chip svg{width:13px;height:13px;flex:none}
.chip.active{background:var(--forest);color:var(--ivory);border-color:var(--forest)}
.chip.active b{color:var(--gold)}
:root[data-theme="dark"] .chip.active,@media(prefers-color-scheme:dark){.chip.active{background:var(--gold);color:#17281E;border-color:var(--gold)}.chip.active b{color:#17281E}}
/* status filter chips (second row) */
.chips.status{margin:-14px 0 26px}
.chip .sdot{width:10px;height:10px;border-radius:3px;background:var(--sc-bright);display:inline-block}
.chip.st-draft.active,.chip.st-ok.active,.chip.st-other.active,.chip.st-await.active,.chip.st-review.active{
  background:var(--sc-bright);border-color:var(--sc-bright);color:var(--sc-ink)}
.chip.st-draft.active b,.chip.st-ok.active b,.chip.st-other.active b,.chip.st-await.active b,.chip.st-review.active b{color:var(--sc-ink);opacity:.75}
.chip.delivered{border-color:#2A9D8F;color:#1f7a70;font-weight:700}
.chip.delivered.active{background:#2A9D8F;border-color:#2A9D8F;color:#062e2a}
.chip.delivered.active b{color:#062e2a;opacity:.75}
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
/* per-status color tokens (Draft=yellow, Approved=green, Awaiting Asset=gray,
   Wiah Review=purple, else red) */
.st-draft{--sc:#E3AE17;--sc-bright:#F5C518;--sc-tint:rgba(245,197,24,.18);--sc-ink:#4a3800}
.st-ok{--sc:#1FA64A;--sc-bright:#1FC24C;--sc-tint:rgba(31,194,76,.15);--sc-ink:#fff}
.st-await{--sc:#7B828C;--sc-bright:#9AA0A9;--sc-tint:rgba(123,130,140,.16);--sc-ink:#fff}
.st-review{--sc:#7A4FD0;--sc-bright:#9163E4;--sc-tint:rgba(145,99,228,.16);--sc-ink:#fff}
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
/* status pill rendered as a <select>; disabled it reads as a static pill, enabled
   (only when served via the Apps Script web app) it edits the live sheet. */
.pill-edit{appearance:none;-webkit-appearance:none;border:none;font:inherit;font-weight:800;
  text-transform:uppercase;letter-spacing:.06em;cursor:default;max-width:160px}
.pill-edit:disabled{opacity:1;color:var(--sc-ink)}
.pill-edit:not(:disabled){cursor:pointer;padding-right:24px;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%23222' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 8px center;background-size:9px}
.pill-edit:not(:disabled):hover{box-shadow:0 1px 3px rgba(0,0,0,.22),0 0 0 2px rgba(0,0,0,.18)}
.pill-edit.saving{opacity:.55}
.pill-edit.saved{box-shadow:0 1px 3px rgba(0,0,0,.22),0 0 0 2px #1FC24C}
.pill-edit option{color:#17281E;background:#fff;font-weight:600;
  text-transform:none;letter-spacing:normal}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(20px);
  background:#3a1a17;color:#ffeede;border:1px solid #B0524A;padding:10px 16px;border-radius:10px;
  font:600 13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  box-shadow:0 4px 16px rgba(0,0,0,.3);z-index:80;opacity:0;pointer-events:none;
  transition:opacity .2s,transform .2s;max-width:80vw}
.toast.ok{background:#173a24;border-color:#1FA64A;color:#e6ffe9}
.toast.show{opacity:1;transform:translateX(-50%)}
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
/* snapping stays OFF until every slide has loaded (see bindCarousel) — while the track
   is still reflowing, Safari re-snaps to whichever slide is nearest and the carousel
   opens mid-way through. 'start' also re-targets less than 'center' under resize. */
.media.carousel .track.nosnap{scroll-snap-type:none}
.media.carousel .track img{flex:0 0 100%;scroll-snap-align:start}
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
footer{margin-top:40px;color:var(--muted);font-size:12px;text-align:center}
/* floating "current week/month" pill (upper-left) + back-to-top button (lower-right) */
.wknow{position:fixed;left:20px;top:16px;z-index:60;background:var(--surface);
  border:1px solid var(--line);color:var(--ink);
  font:700 12px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:8px 13px;
  border-radius:999px;box-shadow:0 2px 12px rgba(20,30,22,.16);white-space:nowrap;
  max-width:46vw;overflow:hidden;text-overflow:ellipsis;
  opacity:0;transform:translateY(-10px);transition:opacity .2s,transform .2s;pointer-events:none}
.wknow.show{opacity:1;transform:none}
.totop{position:fixed;right:20px;bottom:20px;z-index:60;display:flex;align-items:center;gap:10px;
  opacity:0;transform:translateY(10px);transition:opacity .2s,transform .2s;pointer-events:none}
.totop.show{opacity:1;transform:none;pointer-events:auto}
.totop-btn{width:44px;height:44px;border-radius:50%;border:none;cursor:pointer;flex:none;
  background:var(--forest);color:var(--ivory);box-shadow:0 3px 12px rgba(20,30,22,.28);
  display:grid;place-items:center;transition:background .15s,color .15s}
.totop-btn svg{width:20px;height:20px}
.totop-btn:hover{background:var(--gold);color:#17281E}
@media(prefers-color-scheme:dark){.totop-btn{background:var(--gold);color:#17281E}}
:root[data-theme="dark"] .totop-btn{background:var(--gold);color:#17281E}

/* ============================ PLATFORM SIMULATOR ============================
   A full-screen overlay showing the calendar as each platform's real feed. Every image
   here is a CLONE of a node already in the review feed, so the simulator adds no image
   bytes to this file. */
.sim{position:fixed;inset:0;z-index:100;display:flex;align-items:stretch;justify-content:center}
.sim-backdrop{position:absolute;inset:0;background:rgba(8,14,10,.72);backdrop-filter:blur(3px)}
.sim-panel{position:relative;display:flex;flex-direction:column;width:100%;max-width:1180px;
  padding:14px 20px 20px;gap:12px}
.sim-top{display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;color:var(--ivory)}
.sim-tabs{display:flex;gap:8px;flex-wrap:wrap}
.sim-tab{cursor:pointer;border:1px solid rgba(255,255,255,.28);background:rgba(255,255,255,.08);
  color:#F4EFE1;border-radius:999px;padding:7px 16px;font-size:13px;font-weight:700;
  display:inline-flex;gap:7px;align-items:center;transition:background .13s,border-color .13s}
.sim-tab b{color:var(--gold);font-weight:700}
.sim-tab:hover{background:rgba(255,255,255,.16)}
.sim-tab.active{background:var(--gold);border-color:var(--gold);color:#17281E}
.sim-tab.active b{color:#17281E;opacity:.7}
.sim-right{margin-left:auto;display:flex;align-items:center;gap:9px}
.sim-lbl{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:rgba(244,239,225,.65)}
.sim-status{background:rgba(255,255,255,.1);color:#F4EFE1;border:1px solid rgba(255,255,255,.28);
  border-radius:8px;padding:6px 10px;font:600 12px/1.2 inherit;cursor:pointer;max-width:190px}
.sim-status option{color:#17281E;background:#fff}
.sim-btn{cursor:pointer;border:1px solid rgba(255,255,255,.28);background:rgba(255,255,255,.08);
  color:#F4EFE1;border-radius:8px;padding:6px 12px;font:700 12px/1.2 inherit}
.sim-btn.on{background:rgba(198,154,82,.9);border-color:var(--gold);color:#17281E}
.sim-x{cursor:pointer;border:none;background:rgba(255,255,255,.12);color:#F4EFE1;width:34px;
  height:34px;border-radius:50%;font-size:22px;line-height:1;display:grid;place-items:center}
.sim-x:hover{background:var(--terra);color:#fff}
.sim-stage{flex:1;min-height:0;display:flex;align-items:flex-start;justify-content:center;
  overflow:auto;padding-bottom:6px}
/* --- the phone chassis (FR-7); [data-chassis=off] strips it away --- */
.phone{--ph-w:390px;position:relative;width:var(--ph-w);max-width:100%;
  height:min(820px,calc(100vh - 118px));background:#000;border-radius:44px;
  border:11px solid #14181c;box-shadow:0 20px 60px rgba(0,0,0,.55),0 0 0 2px #2b3138;
  display:flex;flex-direction:column;overflow:hidden;flex:none}
.phone-notch{position:absolute;top:7px;left:50%;transform:translateX(-50%);width:112px;
  height:26px;background:#000;border-radius:999px;z-index:6}
.phone-status{display:flex;align-items:center;justify-content:space-between;
  padding:12px 24px 4px;color:#fff;font:700 12.5px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  flex:none;z-index:5}
.phone-status .ps-r{display:inline-flex;align-items:center;gap:5px}
.ps-i{height:11px;width:auto;color:#fff}
.phone[data-chassis="off"]{--ph-w:470px;border:none;border-radius:14px;
  height:min(880px,calc(100vh - 118px));box-shadow:0 14px 40px rgba(0,0,0,.4)}
.phone[data-chassis="off"] .phone-notch,.phone[data-chassis="off"] .phone-status{display:none}
.app{flex:1;min-height:0;display:flex;flex-direction:column;background:#fff;color:#0e0e0e}
.phone[data-platform="tiktok"] .app{background:#000;color:#fff}
/* app chrome: top bar + bottom nav */
.app-top{flex:none;display:flex;align-items:center;gap:10px;padding:9px 14px;
  border-bottom:1px solid #e9e9e9;font-weight:700}
.phone[data-platform="tiktok"] .app-top{border-bottom:none;justify-content:center;gap:18px;
  background:transparent;position:absolute;top:44px;left:0;right:0;z-index:4}
.phone[data-chassis="off"][data-platform="tiktok"] .app-top{top:0}
.app-wordmark{font-family:Georgia,serif;font-size:20px;font-weight:600;letter-spacing:.2px}
.app-top .sp{margin-left:auto;display:inline-flex;gap:15px}
.app-top .sp svg{width:21px;height:21px}
.fb-top{background:#fff;color:#1877F2}
.tt-top-tab{color:rgba(255,255,255,.6);font-size:14.5px;font-weight:700;
  text-shadow:0 1px 3px rgba(0,0,0,.5)}
.tt-top-tab.on{color:#fff;border-bottom:2px solid #fff;padding-bottom:3px}
.app-body{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain}
.app-body::-webkit-scrollbar{width:0}
.app-nav{flex:none;display:flex;align-items:center;justify-content:space-around;
  padding:9px 8px calc(9px + env(safe-area-inset-bottom,0));border-top:1px solid #e9e9e9;
  background:#fff;color:#111}
.app-nav svg{width:23px;height:23px}
.phone[data-platform="tiktok"] .app-nav{background:#000;color:#fff;border-top-color:#222}
.app-nav .navlbl{font-size:9px;font-weight:600;margin-top:2px;display:block;text-align:center}
.app-nav .nv{display:flex;flex-direction:column;align-items:center;opacity:.55}
.app-nav .nv.on{opacity:1}
/* --- IG sub-tabs (Feed / Profile / Reels) --- */
.ig-subtabs{flex:none;display:flex;border-bottom:1px solid #e9e9e9;background:#fff}
.ig-subtabs button{flex:1;cursor:pointer;border:none;background:none;padding:9px 4px;
  font:700 12px/1 inherit;color:#8a8a8a;border-bottom:2px solid transparent;
  text-transform:uppercase;letter-spacing:.06em}
.ig-subtabs button.on{color:#0e0e0e;border-bottom-color:#0e0e0e}
/* --- a simulated post --- */
.s-post{position:relative;border-bottom:1px solid #efefef;background:#fff}
.s-post:last-child{border-bottom:none}
.s-head{display:flex;align-items:center;gap:9px;padding:9px 12px}
.s-ava{width:32px;height:32px;border-radius:50%;flex:none;background-size:cover;
  background-position:center;background-image:radial-gradient(circle at 30% 25%,#2c4a38,#17281E)}
.s-ava.ring{box-shadow:0 0 0 2px #fff,0 0 0 4px #C69A52}
.s-name{font-weight:600;font-size:13.5px}
.s-sub{font-size:11.5px;color:#65676b;display:flex;align-items:center;gap:4px}
.s-sub svg{width:11px;height:11px}
.s-more{margin-left:auto;color:#333;letter-spacing:1px}
/* NOTE: no img sizing rule here on purpose — the existing .media / .media.vframe rules
   already size feed media correctly, and overriding them broke 9:16 video posters. */
.s-acts{display:flex;align-items:center;gap:14px;padding:9px 12px 3px;color:#111}
.s-acts svg{width:23px;height:23px}
.s-acts .bm{margin-left:auto}
.s-likes{padding:1px 12px 0;font-size:13px;font-weight:600}
.s-cap{margin:4px 12px 6px;font-size:13px;line-height:1.42;color:#0e0e0e;word-break:break-word}
.s-cap b{font-weight:600;margin-right:5px}
.s-fold{color:#8a8a8a;cursor:pointer}
.s-tags{margin:0 12px 6px;font-size:12.5px;color:#3a5aa0;word-break:break-word}
.s-cmt{margin:0 12px 4px;font-size:12.5px;color:#8a8a8a}
.s-time{margin:0 12px 11px;font-size:10.5px;color:#8a8a8a;text-transform:uppercase;letter-spacing:.04em}
/* facebook flavour */
.fb .s-cap{margin:0 12px 9px;font-size:13.5px}
.fb-counts{display:flex;align-items:center;gap:6px;padding:7px 12px;font-size:12.5px;
  color:#65676b;border-top:1px solid #f0f0f0}
.fb-counts .rx{display:inline-grid;place-items:center;width:17px;height:17px;border-radius:50%;
  background:#1877F2;color:#fff}
.fb-counts .rx svg{width:10px;height:10px}
.fb-counts .rt{margin-left:auto}
.fb-bar{display:flex;align-items:center;justify-content:space-around;padding:5px 4px;
  border-top:1px solid #e4e6eb;color:#65676b;font-size:12.5px;font-weight:600}
.fb-bar span{display:inline-flex;align-items:center;gap:5px}
.fb-bar svg{width:17px;height:17px}
/* --- vertical full-bleed surfaces (TikTok + IG Reels) --- */
.s-vert{height:100%;overflow-y:auto;scroll-snap-type:y mandatory;overscroll-behavior:contain;
  background:#000}
.s-vert::-webkit-scrollbar{width:0}
/* `align-items:safe center` is the second declaration on purpose: centring is what we want
   while the media fits, but a flex item taller than its line overflows a plain `center` at
   BOTH ends, and the top overflow is unreachable behind overflow:hidden. `safe` degrades to
   start alignment in that case, so a mis-measured slide loses its bottom, never its top.
   Browsers without the keyword drop the line and keep the plain `center` above it. */
.s-slide{position:relative;height:100%;scroll-snap-align:start;scroll-snap-stop:always;
  display:flex;align-items:center;justify-content:center;background:#000;overflow:hidden;
  align-items:safe center}
/* aspect-ratio:auto cancels the 9:16 these nodes carry over from .media.vframe.vert /
   .poster.vert in the review feed. The spec says a specified width AND height wins over
   aspect-ratio, and Blink agrees — but WebKit sizes these flex items from the ratio anyway,
   making the media ~1.78x the slide width where the slide is only as tall as the phone body.
   Centred and clipped, that ate the top of every reel in Safari and nothing in Chrome. */
.s-slide .media{width:100%;height:100%;aspect-ratio:auto;display:flex;align-items:center;
  justify-content:center}
.s-slide .media img{width:100%;height:100%;object-fit:cover}
.s-slide .poster{width:100%;height:100%;aspect-ratio:auto;min-height:0}
.s-rail{position:absolute;right:9px;bottom:96px;display:flex;flex-direction:column;
  align-items:center;gap:15px;color:#fff;z-index:3}
.s-rail .ri{display:flex;flex-direction:column;align-items:center;gap:3px;
  filter:drop-shadow(0 1px 3px rgba(0,0,0,.6))}
.s-rail svg{width:27px;height:27px}
.s-rail .rn{font-size:11px;font-weight:700}
.s-rail .s-ava{width:44px;height:44px;box-shadow:0 0 0 2px rgba(255,255,255,.9);margin-bottom:4px}
.s-vcap{position:absolute;left:12px;right:66px;bottom:86px;color:#fff;z-index:3;
  text-shadow:0 1px 3px rgba(0,0,0,.65)}
.s-vcap .vu{font-weight:700;font-size:14px;margin-bottom:3px}
.s-vcap .vt{font-size:12.5px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:3;
  -webkit-box-orient:vertical;overflow:hidden}
.s-vcap .vm{display:flex;align-items:center;gap:6px;font-size:11.5px;margin-top:6px;opacity:.95}
.s-vcap .vm svg{width:12px;height:12px}
.s-views{position:absolute;left:12px;bottom:60px;color:#fff;font-size:11.5px;font-weight:700;
  z-index:3;text-shadow:0 1px 3px rgba(0,0,0,.6)}
/* --- IG profile tab (absorbed from the removed standalone grid view) --- */
.s-profile{padding:14px 0 0}
.s-phead{display:flex;align-items:center;gap:18px;padding:0 16px 14px}
.s-phead .s-ava{width:74px;height:74px}
.s-pstats{display:flex;gap:20px;font-size:12.5px;text-align:center}
.s-pstats b{display:block;font-size:15px}
.s-pbio{padding:0 16px 14px;font-size:12.5px;line-height:1.45}
.s-pbio .pn{font-weight:700;display:block}
.s-pgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:2px}
/* No status frame on profile tiles — a real IG profile grid is edge-to-edge. The status
   colour still reaches you via the hover chip's dot (.s-meta .sd), which is why each cell
   keeps its st-* class. */
.s-gcell{position:relative;aspect-ratio:1;overflow:hidden;background:#0d0d0d}
.s-gcell img{width:100%;height:100%;object-fit:cover;display:block}
.s-gcorner{position:absolute;top:5px;right:5px;width:17px;height:17px;color:#fff;z-index:2;
  filter:drop-shadow(0 1px 2px rgba(0,0,0,.6))}
.s-gph{width:100%;height:100%;display:grid;place-items:center;color:#C69A52;
  background:linear-gradient(150deg,#22382b,#17281E)}
.s-gph svg{width:28px;height:28px}
/* --- hover-revealed review metadata (FR-9): pristine at rest, no layout shift --- */
.s-meta{position:absolute;top:8px;left:8px;z-index:5;display:flex;align-items:center;gap:6px;
  background:rgba(10,16,12,.86);color:#F4EFE1;border-radius:7px;padding:4px 9px;
  font:700 10.5px/1.3 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;white-space:nowrap;
  opacity:0;transform:translateY(-4px);transition:opacity .13s,transform .13s;pointer-events:none}
.s-post:hover .s-meta,.s-slide:hover .s-meta,.s-gcell:hover .s-meta,
.s-post:focus-within .s-meta,.s-gcell:focus-within .s-meta{opacity:1;transform:none}
.s-meta .sd{width:8px;height:8px;border-radius:2px;background:var(--sc-bright);flex:none}
.s-meta .sm-date{opacity:.75;font-weight:600}
.s-gcell .s-meta{top:4px;left:4px;padding:3px 6px;font-size:9.5px}
.s-post .media,.s-slide .media{position:relative}
/* --- empty state --- */
.s-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;
  height:100%;gap:8px;padding:30px;text-align:center;color:#8a8a8a;font-size:13px}
.phone[data-platform="tiktok"] .s-empty{color:#9aa}
@media(max-width:640px){
  .sim-panel{padding:10px 10px 14px}
  .phone{--ph-w:100%;border-width:8px;border-radius:32px}
  .sim-right{width:100%;margin-left:0}
}
</style>
<div class="wrap">
  <header class="top"><h1>Ghedee Social Calendar</h1><span class="sub">{{SUBTITLE}}</span><span class="who" id="who" hidden></span></header>
  {{CHIPS}}
  {{STATUS_CHIPS}}
  <div id="feed">{{SECTIONS}}</div>
  <footer id="foot">Draft review · nothing here is published. Approve in the calendar, not this page.</footer>
</div>
<!-- platform simulator: a full-screen overlay ABOVE the review feed. The feed stays mounted
     underneath because it is the source of every media node the simulator clones. -->
<div class="sim hide" id="sim" aria-hidden="true">
  <div class="sim-backdrop" data-testid="sim-backdrop"></div>
  <div class="sim-panel" role="dialog" aria-modal="true" aria-label="Platform simulator">
    <div class="sim-top">
      <div class="sim-tabs" role="tablist">
        <button class="sim-tab active" data-p="instagram" role="tab" data-testid="sim-tab-instagram">Instagram <b>0</b></button>
        <button class="sim-tab" data-p="facebook" role="tab" data-testid="sim-tab-facebook">Facebook <b>0</b></button>
        <button class="sim-tab" data-p="tiktok" role="tab" data-testid="sim-tab-tiktok">TikTok <b>0</b></button>
      </div>
      <div class="sim-right">
        <label class="sim-lbl" for="sim-status">Showing</label>
        <select class="sim-status" id="sim-status" data-testid="sim-status"></select>
        <button class="sim-btn on" id="sim-chassis" aria-pressed="true" data-testid="sim-chassis-toggle">Phone frame</button>
        <button class="sim-x" id="sim-close" aria-label="Close simulator" data-testid="sim-close">&times;</button>
      </div>
    </div>
    <div class="sim-stage">
      <div class="phone" id="phone" data-chassis="on" data-platform="instagram">
        <div class="phone-notch"></div>
        <div class="phone-status">
          <span class="ps-t">9:41</span>
          <span class="ps-r">
            <svg viewBox="0 0 20 12" class="ps-i"><path d="M1 9h2v3H1zM5 6h2v6H5zM9 3.5h2V12H9zM13 1h2v11h-2z" fill="currentColor"/></svg>
            <svg viewBox="0 0 20 14" class="ps-i"><path d="M10 12.5 1.5 5a12 12 0 0 1 17 0z" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>
            <svg viewBox="0 0 26 12" class="ps-i"><rect x="1" y="1" width="20" height="10" rx="3" fill="none" stroke="currentColor" stroke-width="1.4"/><rect x="3" y="3" width="14" height="6" rx="1.5" fill="currentColor"/><path d="M23 4.5v3" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>
          </span>
        </div>
        <div class="app" id="app"></div>
      </div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>
<div class="wknow" id="totop-wk"></div>
<div class="totop" id="totop">
  <button class="totop-btn" id="totop-btn" aria-label="Back to top" title="Back to top">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
      stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
  </button>
</div>
<script>
// One binder for every carousel on the page — the review feed's own, and the clones the
// platform simulator builds (which arrive stripped of their nav, so it is re-created here).
// A carousel MUST open on slide 1. It didn't in Safari: slides decode after first layout,
// and every reflow made the mandatory scroll-snap re-target, so the track drifted to slide
// 3 or 4 and the scroll handler then recorded that drift as the current index. Fix is in
// three parts — reserved boxes (width/height on each <img>), snapping held off until the
// images have settled, and scroll events ignored until then.
function bindCarousel(c){
  var track=c.querySelector('.track'); if(!track||track.__bound) return; track.__bound=1;
  var n=track.children.length, i=0, settled=false;
  var prev=c.querySelector('.cnav.prev'), next=c.querySelector('.cnav.next'),
      idxEl=c.querySelector('.cidx'), dots=c.querySelectorAll('.dots span');
  function mk(tag,cls,txt){ var e=document.createElement(tag); if(cls) e.className=cls;
    if(txt!=null) e.textContent=txt; return e; }
  if(!prev){ prev=mk('button','cnav prev','‹');
    prev.setAttribute('aria-label','Previous slide'); c.appendChild(prev); }
  if(!next){ next=mk('button','cnav next','›');
    next.setAttribute('aria-label','Next slide'); c.appendChild(next); }
  if(!idxEl){ var badge=mk('span','badge'); idxEl=mk('b','cidx','1');
    badge.appendChild(idxEl); badge.appendChild(document.createTextNode('/'+n));
    c.appendChild(badge); }
  function sync(){ dots.forEach(function(d,j){d.classList.toggle('on',j===i)});
    if(idxEl) idxEl.textContent=i+1;
    prev.disabled=i===0; next.disabled=i===n-1; }
  function go(k){ i=Math.max(0,Math.min(n-1,k));
    track.scrollTo({left:i*track.clientWidth,behavior:'smooth'}); sync(); }
  prev.addEventListener('click',function(e){e.stopPropagation();go(i-1)});
  next.addEventListener('click',function(e){e.stopPropagation();go(i+1)});
  dots.forEach(function(d,j){d.addEventListener('click',function(){go(j)})});
  var t; track.addEventListener('scroll',function(){clearTimeout(t);t=setTimeout(function(){
    if(!settled) return;                       // load-time drift is not a user swipe
    var k=Math.round(track.scrollLeft/Math.max(1,track.clientWidth));
    if(k!==i){i=k; sync();} },90)});
  track.classList.add('nosnap');
  function settle(){ if(settled) return; settled=true;
    track.scrollLeft=0; i=0; track.classList.remove('nosnap'); sync(); }
  var imgs=[].slice.call(track.querySelectorAll('img')), left=imgs.length;
  function tick(){ if(--left<=0) requestAnimationFrame(settle); }
  if(!left) settle();
  imgs.forEach(function(im){
    if(im.complete) tick();
    else { im.addEventListener('load',tick,{once:true});
           im.addEventListener('error',tick,{once:true}); } });
  setTimeout(settle,4000);                     // never leave snapping off for good
  sync();
}
document.querySelectorAll('.media.carousel').forEach(bindCarousel);
var flt={f:'all', s:'all'};
function statusMatch(s){
  if(flt.s==='all') return true;
  if(flt.s==='needs') return s==='draft'||s==='other';
  if(flt.s==='delivered') return s!=='await';
  return s===flt.s;
}
function viewMatch(c){
  if(flt.f==='all') return true;
  if(flt.f==='reel') return c.dataset.reel==='1';
  if(flt.f==='carousel') return c.dataset.carousel==='1';
  return c.dataset.platform===flt.f;
}
function applyFilter(){
  document.querySelectorAll('.card').forEach(function(c){
    var vis=viewMatch(c) && statusMatch(c.dataset.status);
    c.classList.toggle('hide', !vis);
  });
  document.querySelectorAll('.week').forEach(function(w){
    w.classList.toggle('hide', !w.querySelector('.card:not(.hide)')); });
  if(window.__syncTop) window.__syncTop();
  // the simulator reads the SAME flt.s, so one filter drives both views
  if(window.__simRefresh) window.__simRefresh();
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
// ---- interactive status editing (only when served via the Apps Script web app) ----
// The page runs inside HtmlService's sandbox, so google.script.run can call the
// server-side setPostStatus(sheetId,rowId,status) directly — no fetch/CORS/token.
var SHEET_ID = "{{SHEET_ID}}";
function statusKind(s){
  s=(s||'').trim().toLowerCase();
  if(s==='draft') return 'draft';
  if(s==='approved') return 'ok';
  if(s==='awaiting asset') return 'await';
  if(s==='wiah review') return 'review';
  return 'other';
}
function applyKind(el, kind){
  el.classList.remove('st-draft','st-ok','st-await','st-review','st-other');
  el.classList.add('st-'+kind); el.dataset.status=kind;
}
function paintRow(rowId, statusStr){
  var kind=statusKind(statusStr);
  document.querySelectorAll('.pill-edit').forEach(function(sel){
    if(sel.dataset.rowid===rowId){ var card=sel.closest('.card'); if(card) applyKind(card,kind); }
  });
  document.querySelectorAll('.gcell').forEach(function(g){
    if(g.dataset.rowid===rowId) applyKind(g,kind);
  });
}
function setChipCount(sel, n){ var b=document.querySelector('.chip'+sel+' b'); if(b) b.textContent=n; }
function recount(){
  var k={draft:0,ok:0,await:0,review:0,other:0}, total=0;
  document.querySelectorAll('#feed .card').forEach(function(c){
    total++; if(k[c.dataset.status]!=null) k[c.dataset.status]++; });
  setChipCount('[data-s="all"]', total);
  setChipCount('[data-s="draft"]', k.draft); setChipCount('[data-s="ok"]', k.ok);
  setChipCount('[data-s="await"]', k.await); setChipCount('[data-s="review"]', k.review);
  setChipCount('[data-s="other"]', k.other);
  setChipCount('[data-s="delivered"]', total-k.await);
  setChipCount('[data-s="needs"]', k.draft+k.other);
  document.querySelectorAll('#feed .week').forEach(function(w){
    var cards=w.querySelectorAll('.card'), ok=0;
    cards.forEach(function(c){ if(c.dataset.status==='ok') ok++; });
    var n=cards.length, pct=n?Math.round(100*ok/n):0;
    var cnt=w.querySelector('.wk-count'); if(cnt) cnt.textContent=ok+'/'+n+' approved';
    var bar=w.querySelector('.wk-bar i'); if(bar) bar.style.width=pct+'%';
  });
}
var toastT;
function toast(msg, ok){
  var t=document.getElementById('toast'); if(!t) return;
  t.textContent=msg; t.classList.toggle('ok', !!ok); t.classList.add('show');
  clearTimeout(toastT); toastT=setTimeout(function(){t.classList.remove('show');}, ok?1800:4500);
}
function onStatusChange(sel){
  var rowId=sel.dataset.rowid, prev=sel.dataset.status||'', next=sel.value;
  if(next===prev) return;
  sel.disabled=true; sel.classList.add('saving');
  paintRow(rowId, next); recount();  // optimistic
  function revert(msg){
    sel.value=prev; paintRow(rowId, prev); recount();
    sel.disabled=false; sel.classList.remove('saving');
    toast(msg, false);
  }
  try {
    google.script.run
      .withSuccessHandler(function(res){
        if(window.console) console.log('setPostStatus result', res);
        if(!(res && res.ok)){
          // server returned but didn't confirm a write (e.g. an older endpoint)
          revert('Save not confirmed for '+rowId+' — the sheet may be unchanged.');
          return;
        }
        sel.dataset.status=next; sel.disabled=false;
        sel.classList.remove('saving'); sel.classList.add('saved');
        setTimeout(function(){sel.classList.remove('saved');},1200);
        toast('Saved '+rowId+' → '+res.newValue+' · '+res.sheetName+' row '+res.row, true);
        applyFilter();
      })
      .withFailureHandler(function(err){
        revert('Could not save '+rowId+': '+((err&&err.message)||err));
      })
      .setPostStatus(SHEET_ID, rowId, next);
  } catch(e){
    // e.g. the setPostStatus endpoint isn't deployed — never leave the pill stuck.
    revert('Status editing is unavailable: '+((e&&e.message)||e));
  }
}
function applyStatusValue(rowId, statusStr){
  // Reflect a status string coming from the sheet onto the pill + card + grid tile.
  var kind=statusKind(statusStr);
  document.querySelectorAll('.pill-edit').forEach(function(sel){
    if(sel.dataset.rowid!==rowId) return;
    var has=false;
    for(var i=0;i<sel.options.length;i++){ if(sel.options[i].value===statusStr){ has=true; break; } }
    if(!has && statusStr){ var o=document.createElement('option'); o.textContent=statusStr; sel.appendChild(o); }
    sel.value=statusStr; sel.dataset.status=statusStr;
    var card=sel.closest('.card'); if(card) applyKind(card, kind);
  });
  document.querySelectorAll('.gcell').forEach(function(g){
    if(g.dataset.rowid===rowId) applyKind(g, kind);
  });
}
function hydrateStatuses(){
  // The served HTML is a static snapshot; pull the sheet's CURRENT statuses on load so a
  // refresh (or a status edited straight in the sheet) is reflected without a rebuild.
  if(typeof google.script.run.getPostStatuses !== 'function') return;
  google.script.run
    .withSuccessHandler(function(map){
      if(!map) return;
      Object.keys(map).forEach(function(rowId){ applyStatusValue(rowId, map[rowId]); });
      recount(); applyFilter();
    })
    .withFailureHandler(function(){ /* keep the static snapshot on failure */ })
    .getPostStatuses(SHEET_ID);
}
function showViewer(){
  // Who's viewing (+ whether they can reach the sheet) — surfaces access problems: a
  // teammate signed into the wrong Google account, or one the sheet isn't shared with.
  if(typeof google.script.run.getViewerInfo !== 'function') return;
  var el=document.getElementById('who'); if(!el) return;
  google.script.run
    .withSuccessHandler(function(info){
      if(!info) return;
      el.textContent='';
      var noAccess = info.canOpenSheet===false;
      var email = info.email;  // ACTIVE (viewing) user; blank unless the app can identify them
      el.appendChild(document.createTextNode((noAccess||!email?'⚠ ':'')+'Signed in as '));
      var b=document.createElement('b');
      b.textContent = email || 'account not detected';
      el.appendChild(b);
      var tip=[];
      if(info.effective) tip.push('script runs as: '+info.effective);
      if(info.sheetName) tip.push('sheet: '+info.sheetName);
      if(noAccess){
        el.appendChild(document.createTextNode(' — no access to the calendar sheet'));
        if(info.sheetError) tip.push(info.sheetError);
        el.classList.add('warn');
      } else if(!email){
        // Google only reveals the viewer's email when the web app's access is limited to
        // the Workspace domain (not "Anyone"). Point at that instead of blaming the user.
        el.appendChild(document.createTextNode(' — limit web-app access to your domain to show it'));
        el.classList.add('warn');
      }
      if(info.switchAccountUrl){
        // One-click recovery for viewers with several Google accounts: Google's account
        // chooser, returning to this same app. Opens in a new tab (the page is sandboxed).
        el.appendChild(document.createTextNode(' · '));
        var a=document.createElement('a');
        a.href=info.switchAccountUrl; a.target='_blank'; a.rel='noopener';
        a.textContent='Switch account';
        el.appendChild(a);
      }
      if(tip.length) el.title=tip.join(' · ');
      el.hidden=false;
    })
    .withFailureHandler(function(){ /* leave the badge hidden if the probe fails */ })
    .getViewerInfo(SHEET_ID);
}
(function initLive(){
  if(!(window.google && google.script && google.script.run && SHEET_ID)) return;
  showViewer();  // show the viewer's identity whether or not editing is enabled
  // Enable editing only if the setPostStatus endpoint is deployed — otherwise stay
  // read-only rather than offering a dropdown whose changes can't be saved.
  if(typeof google.script.run.setPostStatus === 'function'){
    document.body.classList.add('editable');
    document.querySelectorAll('.pill-edit').forEach(function(sel){
      sel.disabled=false;
      sel.addEventListener('change',function(){ onStatusChange(sel); });
    });
    var f=document.getElementById('foot');
    if(f) f.textContent='Live editing on · status changes save straight to the calendar sheet.';
  }
  hydrateStatuses();  // reflect the sheet's current statuses on every load
})();
// ======================= PLATFORM SIMULATOR =======================
// Renders the calendar as each platform's real feed. The key move: every image shown here
// is a cloneNode() of a media element already in the review feed, so the simulator adds
// ZERO image bytes to this file — cloning copies the src STRING, not the data.
var SIM_POSTS = {{SIM_POSTS}};
var ICONS = {{ICONS}};
(function(){
  var sim=document.getElementById('sim'); if(!sim) return;
  var app=document.getElementById('app'), phone=document.getElementById('phone'),
      openBtn=document.getElementById('sim-open'), selStatus=document.getElementById('sim-status');
  // Only the VISIBLE surface is ever built (render() is called on open and on each tab
  // switch), so opening the simulator never stalls on constructing all five at once.
  var S={open:false, platform:'instagram', igTab:'feed', chassis:true};
  // row id -> review card, resolved once (avoids re-querying and any id-escaping concerns)
  var CARD={};
  document.querySelectorAll('#feed .card').forEach(function(c){ CARD[c.dataset.rowid]=c; });

  var STATUS_OPTS=[['all','All statuses'],['ok','Approved'],['draft','Draft'],
    ['await','Awaiting Asset'],['review','Wiah Review'],['other','Other'],
    ['delivered','Asset Delivered'],['needs','Needs review']];

  function fmtN(n){
    if(n>=1000000) return (n/1000000).toFixed(1).replace(/\.0$/,'')+'M';
    if(n>=1000) return (n/1000).toFixed(1).replace(/\.0$/,'')+'K';
    return String(n);
  }
  function el(tag, cls, txt){
    var e=document.createElement(tag);
    if(cls) e.className=cls;
    if(txt!=null) e.textContent=txt;   // textContent, never innerHTML, for sheet-authored copy
    return e;
  }
  function icon(name, cls){
    var s=el('span', cls||'ico');
    s.innerHTML=ICONS[name]||'';       // trusted: our own constant icon table
    return s;
  }
  // --- membership + ordering (D3). SIM_POSTS is already sorted newest-first. ---
  function posts(platform, only){
    return SIM_POSTS.filter(function(p){
      if(p.p!==platform) return false;
      if(only==='reel' && !p.reel) return false;
      return statusMatch(p.st);        // the review page's own status filter
    });
  }
  function mediaClone(p){
    var card=CARD[p.id]; if(!card) return null;
    var m=card.querySelector('.media'); if(!m) return null;
    var c=m.cloneNode(true);
    c.querySelectorAll('.cnav,.badge,.dots').forEach(function(n){ n.remove(); });
    return c;
  }
  function imgClone(p){
    var card=CARD[p.id]; if(!card) return null;
    var i=card.querySelector('.media img');
    return i? i.cloneNode(true) : null;
  }
  function meta(p){
    var m=el('div','s-meta');
    m.appendChild(el('i','sd'));
    m.appendChild(el('span',null,p.id));
    if(p.day) m.appendChild(el('span','sm-date',p.day));
    return m;
  }
  function avatar(cls){
    var a=el('span','s-ava '+(cls||''));
    var src=document.querySelector('.avatar.ava-photo');
    if(src){ var bg=getComputedStyle(src).backgroundImage; if(bg && bg!=='none') a.style.backgroundImage=bg; }
    return a;
  }
  function caption(p, withHandle){
    if(!p.cap) return null;
    var c=el('p','s-cap');
    if(withHandle) c.appendChild(el('b',null,p.handle));
    var full=p.cap, fold=125;
    if(full.length>fold){
      c.appendChild(document.createTextNode(full.slice(0,fold).trim()+'… '));
      var more=el('span','s-fold','more');
      more.addEventListener('click',function(){
        c.textContent=''; if(withHandle) c.appendChild(el('b',null,p.handle));
        c.appendChild(document.createTextNode(full));
      });
      c.appendChild(more);
    } else { c.appendChild(document.createTextNode(full)); }
    return c;
  }
  function wrapPost(p, cls){
    var a=el('article','s-post st-'+p.st+(cls?' '+cls:''));
    a.dataset.rowid=p.id;
    return a;
  }
  // ---------------- Instagram: Feed ----------------
  function buildIGFeed(host){
    var list=posts('instagram');
    if(!list.length) return host.appendChild(empty());
    list.forEach(function(p){
      var a=wrapPost(p);
      var h=el('div','s-head');
      h.appendChild(avatar('ring'));
      h.appendChild(el('span','s-name',p.handle));
      h.appendChild(el('span','s-more','···'));
      a.appendChild(h);
      var m=mediaClone(p); if(m) a.appendChild(m);
      var acts=el('div','s-acts');
      ['heart','comment','share'].forEach(function(n){ acts.appendChild(icon(n)); });
      acts.appendChild(icon('bookmark','ico bm'));
      a.appendChild(acts);
      a.appendChild(el('div','s-likes',fmtN(p.eng.likes)+' likes'));
      var c=caption(p,true); if(c) a.appendChild(c);
      if(p.tags) a.appendChild(el('p','s-tags',p.tags));
      if(p.eng.comments) a.appendChild(el('div','s-cmt','View all '+fmtN(p.eng.comments)+' comments'));
      if(p.rel) a.appendChild(el('div','s-time',p.rel+' ago'));
      a.appendChild(meta(p));
      host.appendChild(a);
    });
  }
  // ---------------- Instagram: Profile grid ----------------
  function buildIGProfile(host){
    var list=posts('instagram');
    var wrap=el('div','s-profile');
    var head=el('div','s-phead');
    head.appendChild(avatar('ring'));
    var st=el('div','s-pstats');
    [[list.length,'posts'],[1284,'followers'],[312,'following']].forEach(function(pair){
      var d=el('div'); d.appendChild(el('b',null,fmtN(pair[0])));
      d.appendChild(document.createTextNode(pair[1])); st.appendChild(d);
    });
    head.appendChild(st); wrap.appendChild(head);
    var bio=el('div','s-pbio');
    bio.appendChild(el('span','pn','Ghedee Philosophy'));
    bio.appendChild(document.createTextNode(
      'The 18 Universal Laws · a philosophy of living, with Wiah.'));
    wrap.appendChild(bio);
    if(!list.length){ wrap.appendChild(empty()); host.appendChild(wrap); return; }
    var grid=el('div','s-pgrid');
    list.forEach(function(p){
      var cell=el('div','s-gcell st-'+p.st);
      var img=imgClone(p);
      if(img){ cell.appendChild(img); }
      else {
        var ph=el('div','s-gph');
        ph.appendChild(icon(p.kind==='none'&&p.reel?'film':'play','')); cell.appendChild(ph);
      }
      if(p.car) cell.appendChild(icon('stack','s-gcorner'));
      else if(p.kind==='video'||p.reel) cell.appendChild(icon('reel','s-gcorner'));
      cell.appendChild(meta(p));
      grid.appendChild(cell);
    });
    wrap.appendChild(grid); host.appendChild(wrap);
  }
  // ---------------- vertical surfaces: IG Reels + TikTok ----------------
  function buildVertical(host, platform, only, flavour){
    var list=posts(platform, only);
    if(!list.length) return host.appendChild(empty());
    var v=el('div','s-vert');
    list.forEach(function(p){
      var s=el('div','s-slide st-'+p.st);
      s.dataset.rowid=p.id;
      var m=mediaClone(p); if(m) s.appendChild(m);
      var rail=el('div','s-rail');
      rail.appendChild(avatar(''));
      [['heart',p.eng.likes],['comment',p.eng.comments],
       ['bookmark',p.eng.saves],['share',p.eng.shares]].forEach(function(pair){
        var ri=el('div','ri'); ri.appendChild(icon(pair[0]));
        ri.appendChild(el('span','rn',fmtN(pair[1]))); rail.appendChild(ri);
      });
      s.appendChild(rail);
      var cap=el('div','s-vcap');
      cap.appendChild(el('div','vu',p.handle));
      if(p.cap) cap.appendChild(el('div','vt',p.cap));
      var mus=el('div','vm'); mus.appendChild(icon('music',''));
      mus.appendChild(el('span',null,'original audio · '+p.handle));
      cap.appendChild(mus);
      s.appendChild(cap);
      if(p.eng.views) s.appendChild(el('div','s-views',fmtN(p.eng.views)+' views'));
      s.appendChild(meta(p));
      v.appendChild(s);
    });
    host.appendChild(v);
  }
  // ---------------- Facebook ----------------
  function buildFB(host){
    var list=posts('facebook');
    if(!list.length) return host.appendChild(empty());
    list.forEach(function(p){
      var a=wrapPost(p,'fb');
      var h=el('div','s-head');
      h.appendChild(avatar(''));
      var nm=el('div');
      nm.appendChild(el('div','s-name','Wiah at Ghedee Philosophy'));
      var sub=el('div','s-sub');
      sub.appendChild(el('span',null,p.rel||p.day));
      sub.appendChild(icon('globe','')); nm.appendChild(sub);
      h.appendChild(nm); h.appendChild(el('span','s-more','···'));
      a.appendChild(h);
      var c=caption(p,false); if(c) a.appendChild(c);      // FB: caption ABOVE the media
      var m=mediaClone(p); if(m) a.appendChild(m);
      var counts=el('div','fb-counts');
      counts.appendChild(icon('like','rx'));
      counts.appendChild(el('span',null,fmtN(p.eng.likes)));
      counts.appendChild(el('span','rt',fmtN(p.eng.comments)+' comments · '+
        fmtN(p.eng.shares)+' shares'));
      a.appendChild(counts);
      var bar=el('div','fb-bar');
      [['like','Like'],['comment','Comment'],['share','Share']].forEach(function(pair){
        var s=el('span'); s.appendChild(icon(pair[0],''));
        s.appendChild(document.createTextNode(pair[1])); bar.appendChild(s);
      });
      a.appendChild(bar);
      a.appendChild(meta(p));
      host.appendChild(a);
    });
  }
  function empty(){
    var e=el('div','s-empty');
    e.appendChild(el('div',null,'No posts match the current filter.'));
    e.appendChild(el('div',null,'Change "Showing" above to see more.'));
    return e;
  }
  // ---------------- app chrome ----------------
  function topBar(platform){
    var t=el('div','app-top'+(platform==='facebook'?' fb-top':''));
    if(platform==='instagram'){
      t.appendChild(el('span','app-wordmark','Instagram'));
      var sp=el('span','sp'); sp.appendChild(icon('heart','')); sp.appendChild(icon('inbox',''));
      t.appendChild(sp);
    } else if(platform==='facebook'){
      t.appendChild(el('span','app-wordmark','facebook'));
      var sp2=el('span','sp'); sp2.appendChild(icon('search','')); sp2.appendChild(icon('inbox',''));
      t.appendChild(sp2);
    } else {
      t.appendChild(el('span','tt-top-tab','Following'));
      t.appendChild(el('span','tt-top-tab on','For You'));
    }
    return t;
  }
  function navBar(platform){
    var n=el('div','app-nav');
    var items = platform==='tiktok'
      ? [['home','Home',1],['search','Discover',0],['add','',0],['inbox','Inbox',0],['person','Profile',0]]
      : [['home','',1],['search','',0],['add','',0],['heart','',0],['person','',0]];
    items.forEach(function(it){
      var d=el('div','nv'+(it[2]?' on':''));
      d.appendChild(icon(it[0],''));
      if(it[1]) d.appendChild(el('span','navlbl',it[1]));
      n.appendChild(d);
    });
    return n;
  }
  // ---------------- render ----------------
  function render(){
    phone.dataset.platform=S.platform;
    app.textContent='';
    app.appendChild(topBar(S.platform));
    if(S.platform==='instagram'){
      var tabs=el('div','ig-subtabs');
      [['feed','Feed'],['profile','Profile'],['reels','Reels']].forEach(function(pair){
        var b=el('button',(S.igTab===pair[0]?'on':''),pair[1]);
        b.setAttribute('data-testid','sim-igtab-'+pair[0]);
        b.addEventListener('click',function(){ S.igTab=pair[0]; render(); });
        tabs.appendChild(b);
      });
      app.appendChild(tabs);
    }
    var body=el('div','app-body');
    if(S.platform==='instagram'){
      if(S.igTab==='feed') buildIGFeed(body);
      else if(S.igTab==='profile') buildIGProfile(body);
      else buildVertical(body,'instagram','reel');
    } else if(S.platform==='facebook'){ buildFB(body); }
    else { buildVertical(body,'tiktok'); }
    if(S.platform==='tiktok'||(S.platform==='instagram'&&S.igTab==='reels')){
      body.style.overflow='hidden';   // the inner .s-vert owns the scrolling
    }
    app.appendChild(body);
    app.appendChild(navBar(S.platform));
    rebindCarousels(body);
    counts();
  }
  function counts(){
    document.querySelectorAll('.sim-tab').forEach(function(t){
      var b=t.querySelector('b'); if(b) b.textContent=posts(t.dataset.p).length;
    });
  }
  // Cloned carousels arrive without their bindings; re-attach the page's own binder so a
  // multi-slide post swipes in the simulator exactly as it does in the review feed.
  function rebindCarousels(root){
    root.querySelectorAll('.media.carousel').forEach(bindCarousel);
  }
  // ---- video posts: poster frame + a play button that opens the clip on Drive ----
  // There is deliberately NO inline playback. Both embed routes were tried and measured:
  //   * a native <video> against drive.usercontent.google.com/download — Drive answers a
  //     browser's `Origin: null` (any file:// page) with 403 and no Access-Control-Allow-Origin,
  //     and the no-cors path is blocked by its Cross-Origin-Resource-Policy: same-site.
  //   * Drive's own /file/d/<id>/preview player — works, but it is a player PAGE and draws a
  //     toolbar above the picture, which reads as a black band across the top of a feed post.
  // So the choice was a broken embed, a fake-looking band, or a clean still. A paused feed
  // shows stills anyway, so the cloned poster + its existing <a> to Drive is the honest
  // answer, and needs no code at all — the anchor comes across in the clone.

  // ---------------- controls ----------------
  function open(){
    S.open=true; sim.classList.remove('hide'); sim.setAttribute('aria-hidden','false');
    document.body.style.overflow='hidden';
    syncStatusSelect(); render();
    document.getElementById('sim-close').focus();
  }
  function close(){
    S.open=false; sim.classList.add('hide'); sim.setAttribute('aria-hidden','true');
    document.body.style.overflow='';        // review feed was never unmounted -> scroll
    if(openBtn) openBtn.focus();            // position and filters are inherently intact
  }
  if(openBtn) openBtn.addEventListener('click',open);
  document.getElementById('sim-close').addEventListener('click',close);
  sim.querySelector('.sim-backdrop').addEventListener('click',close);
  document.addEventListener('keydown',function(e){ if(e.key==='Escape'&&S.open) close(); });
  document.querySelectorAll('.sim-tab').forEach(function(t){
    t.addEventListener('click',function(){
      document.querySelectorAll('.sim-tab').forEach(function(x){x.classList.remove('active')});
      t.classList.add('active'); S.platform=t.dataset.p; render();
    });
  });
  var chassisBtn=document.getElementById('sim-chassis');
  chassisBtn.addEventListener('click',function(){
    S.chassis=!S.chassis;
    phone.dataset.chassis=S.chassis?'on':'off';
    chassisBtn.classList.toggle('on',S.chassis);
    chassisBtn.setAttribute('aria-pressed',String(S.chassis));
  });
  // The overlay covers the page's status chips, so it carries a mirror of them. Both write
  // the SAME flt.s, so the two views can never disagree.
  STATUS_OPTS.forEach(function(pair){
    var o=document.createElement('option'); o.value=pair[0]; o.textContent=pair[1];
    selStatus.appendChild(o);
  });
  function syncStatusSelect(){ selStatus.value=flt.s; }
  selStatus.addEventListener('change',function(){
    flt.s=selStatus.value;
    document.querySelectorAll('.chip[data-s]').forEach(function(b){
      b.classList.toggle('active', b.dataset.s===flt.s); });
    applyFilter();
  });
  window.__simRefresh=function(){ syncStatusSelect(); if(S.open) render(); else counts(); };
  counts();
})();
// back-to-top button + live "current week/month" indicator
(function(){
  var fab=document.getElementById('totop'); if(!fab) return;
  var wkEl=document.getElementById('totop-wk'), btn=document.getElementById('totop-btn'),
      feed=document.getElementById('feed');
  btn.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});
  function currentWeek(){
    // the last visible week whose top has scrolled above the reading line is "current"
    var secs=feed.querySelectorAll('.week:not(.hide)'), cur=null;
    for(var i=0;i<secs.length;i++){
      if(secs[i].getBoundingClientRect().top<=140) cur=secs[i]; else break;
    }
    if(!cur && secs.length) cur=secs[0];
    var lbl=cur && cur.querySelector('.wk-label');
    return lbl ? lbl.textContent : '';
  }
  function update(){
    var inGrid=feed.classList.contains('hide');
    var scrolled=window.scrollY>360;
    fab.classList.toggle('show', scrolled);
    var w=inGrid ? '' : currentWeek();
    wkEl.textContent=w;
    wkEl.classList.toggle('show', scrolled && !!w);
  }
  window.__syncTop=update;
  var pending=false;
  window.addEventListener('scroll',function(){
    if(pending) return; pending=true;
    requestAnimationFrame(function(){pending=false; update();});
  },{passive:true});
  update();
})();
</script>
"""
