"""social.specs — the canonical per-platform, per-post-type standards table.

Single source of truth for what a social post's asset and caption SHOULD look like on
each platform, so generation and the audit can never disagree:

  - ``rules.plan_visual`` reads ``target_aspect`` here to generate platform-correct
    shapes (e.g. a non-Reel video is 9:16 on Instagram/TikTok, 16:9 on Facebook).
  - ``audit`` reads the full spec (accepted aspect ratios, min resolution, file-size
    cap, caption/hashtag caps, carousel slide caps) to judge the DELIVERED asset.

Pure/deterministic, no I/O (same contract as rules.py) — unit-testable offline.

Post types (derived from Platform + Format + Visual Type, never stored in the sheet):
  image_post · video_post · reel · carousel.

Scope note: **Stories are intentionally out of scope** — the calendar's Format enum is
Post / Reel / Carousel and does not schedule Stories. Add a `story` post type + spec
here if that ever changes.

Numbers verified 2026-07 against current platform specs & reputable spec guides (see
SOURCES). They are guidance thresholds for an audit, not a billing contract; update this
table (and bump `verified`) when a platform changes its specs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VERIFIED = "2026-07"

SOURCES = {
    "instagram": "help.instagram.com + Buffer/HeyOrca IG spec guides (2026)",
    "facebook": "facebook help + HeyOrca/BrandGhost FB spec guides (2026)",
    "tiktok": "TikTok Creator/help + PostFast/UseVisuals TikTok spec guides (2026)",
}

# --- post-type keys --------------------------------------------------------
IMAGE_POST = "image_post"
VIDEO_POST = "video_post"
REEL = "reel"
CAROUSEL = "carousel"
POST_TYPES = (IMAGE_POST, VIDEO_POST, REEL, CAROUSEL)


@dataclass(frozen=True)
class MediaSpec:
    """The asset standard for one (platform, post_type) cell.

    ``accepted_aspects`` is the set the audit PASSes; ``target_aspect`` is the single
    ratio generation aims for (always a member of ``accepted_aspects``). ``min_short_edge_px``
    is the minimum acceptable resolution on the short edge; ``max_file_mb`` the upload cap.
    Carousel cells carry slide-count bounds.
    """
    target_aspect: str
    accepted_aspects: tuple[str, ...]
    min_short_edge_px: int
    max_file_mb: float
    carousel_min_slides: int | None = None
    carousel_max_slides: int | None = None
    # Video length bounds (seconds); None for stills. Over max -> FAIL (won't post as this
    # type); under min -> WARN (short but postable).
    min_seconds: float | None = None
    max_seconds: float | None = None
    note: str = ""


@dataclass(frozen=True)
class CaptionSpec:
    """The caption standard for a platform (same across its post types)."""
    caption_max: int          # hard platform character cap -> FAIL beyond
    fold: int                 # chars visible before the "…more" truncation
    hashtag_max: int | None   # hard cap, or None when the platform sets no hard limit


# --- the matrix ------------------------------------------------------------
# Only IG/TikTok video_post differ from the legacy flat rule (16:9 -> 9:16); every other
# cell reproduces the shape generation already produced, so this refactor is surgical.
SPECS: dict[str, dict[str, MediaSpec]] = {
    "instagram": {
        # Single feed image: 1:1 or 4:5 (portrait 4:5 gets the most feed real estate);
        # 1.91:1 landscape is accepted. IG images cap at 8 MB.
        IMAGE_POST: MediaSpec("1:1", ("1:1", "4:5", "1.91:1"), 1080, 8),
        # Instagram has NO separate feed video — an uploaded video publishes as a Reel.
        # So a Post+video row is normalized to a 9:16 Reel (see classify()).
        VIDEO_POST: MediaSpec("9:16", ("9:16",), 1080, 650, min_seconds=3, max_seconds=180,
                              note="Instagram has no feed video; publishes as a Reel (9:16)."),
        REEL: MediaSpec("9:16", ("9:16",), 1080, 650, min_seconds=3, max_seconds=180),
        # Up to 20 slides; the first slide locks the ratio (4:5 recommended).
        CAROUSEL: MediaSpec("4:5", ("1:1", "4:5"), 1080, 8,
                            carousel_min_slides=2, carousel_max_slides=20),
    },
    "facebook": {
        IMAGE_POST: MediaSpec("1:1", ("1:1", "4:5", "1.91:1"), 1080, 30),
        # Facebook is the one platform with a genuine landscape feed-video format (up to 240 min).
        VIDEO_POST: MediaSpec("16:9", ("16:9", "4:5", "1:1"), 1080, 4096,
                              min_seconds=1, max_seconds=14400),
        REEL: MediaSpec("9:16", ("9:16",), 1080, 1024, min_seconds=3, max_seconds=180),
        CAROUSEL: MediaSpec("4:5", ("1:1", "4:5"), 1080, 30,
                            carousel_min_slides=2, carousel_max_slides=10),
    },
    "tiktok": {
        # TikTok has no native single-image post — images live only in Photo Mode
        # carousels. A lone image is accepted but flagged (see classify()).
        IMAGE_POST: MediaSpec("1:1", ("9:16", "1:1", "4:5"), 1080, 500,
                              note="TikTok has no single-image post; use a Photo Mode carousel."),
        # All TikTok video is one vertical (9:16) format — there is no 16:9 feed video. Up to 10 min.
        VIDEO_POST: MediaSpec("9:16", ("9:16",), 1080, 500, min_seconds=3, max_seconds=600),
        REEL: MediaSpec("9:16", ("9:16",), 1080, 500, min_seconds=3, max_seconds=600),
        # Photo Mode: 4–35 images, 9:16 best (1:1 / 4:5 also supported), 500 MB total.
        CAROUSEL: MediaSpec("4:5", ("9:16", "1:1", "4:5"), 1080, 500,
                            carousel_min_slides=4, carousel_max_slides=35),
    },
}

# Legacy-preserving fallback for a blank/unknown Platform: reproduces the old flat rule
# (non-Reel video -> 16:9), so unrecognized platforms never change behaviour.
_GENERIC = {
    IMAGE_POST: MediaSpec("1:1", ("1:1", "4:5", "1.91:1"), 1080, 30),
    VIDEO_POST: MediaSpec("16:9", ("16:9", "9:16", "1:1", "4:5"), 1080, 4096),
    REEL: MediaSpec("9:16", ("9:16",), 1080, 1024, min_seconds=3, max_seconds=180),
    CAROUSEL: MediaSpec("4:5", ("1:1", "4:5"), 1080, 30,
                        carousel_min_slides=2, carousel_max_slides=20),
}

CAPTIONS: dict[str, CaptionSpec] = {
    "instagram": CaptionSpec(caption_max=2200, fold=125, hashtag_max=30),
    "facebook": CaptionSpec(caption_max=63206, fold=477, hashtag_max=None),
    "tiktok": CaptionSpec(caption_max=4000, fold=100, hashtag_max=None),
}
_GENERIC_CAPTION = CaptionSpec(caption_max=2200, fold=125, hashtag_max=30)


# --- resolution helpers ----------------------------------------------------
def norm_platform(platform: str | None) -> str:
    """Sheet Platform value -> canonical key ('instagram' | 'facebook' | 'tiktok'), or
    'generic' for anything blank/unrecognized (legacy-preserving)."""
    p = (platform or "").strip().lower()
    if p.startswith("instagram") or p == "ig":
        return "instagram"
    if p.startswith("facebook") or p == "fb":
        return "facebook"
    if p.startswith("tiktok") or p.startswith("tik tok") or p == "tt":
        return "tiktok"
    return "generic"


def _table(platform_key: str) -> dict[str, MediaSpec]:
    return SPECS.get(platform_key, _GENERIC)


def kind_to_post_type(kind: str, is_reel: bool) -> str:
    """Map a generation ``kind`` (image|video|carousel) + reel flag onto a post type."""
    if kind == "image":
        return IMAGE_POST
    if kind == "carousel":
        return CAROUSEL
    # kind == "video"
    return REEL if is_reel else VIDEO_POST


def target_aspect(platform: str | None, kind: str, is_reel: bool) -> str:
    """The aspect ratio generation should target for this platform + kind. This is what
    ``rules.plan_visual`` calls, so IG/TikTok non-Reel video comes out 9:16 (Facebook 16:9)."""
    if kind not in ("image", "video", "carousel"):
        return ""
    spec = _table(norm_platform(platform)).get(kind_to_post_type(kind, is_reel))
    return spec.target_aspect if spec else ""


def media_spec(platform: str | None, post_type: str) -> MediaSpec:
    """The MediaSpec for a (platform, post_type). Falls back to the generic table."""
    return _table(norm_platform(platform)).get(post_type) or _GENERIC[post_type]


def caption_spec(platform: str | None) -> CaptionSpec:
    return CAPTIONS.get(norm_platform(platform), _GENERIC_CAPTION)


def classify(platform: str | None, fmt: str | None, kind: str,
             is_reel: bool | None = None) -> tuple[str, list[str]]:
    """Resolve a row to its audited (post_type, issues).

    ``issues`` holds platform/format-validity warnings (never a crash): the row is still
    classified and measurable, but the mismatch is surfaced — e.g. an Instagram Post+video
    normalized to a Reel, or a single image aimed at TikTok. Used by the audit; generation
    only needs target_aspect().
    """
    p = norm_platform(platform)
    f = (fmt or "").strip().lower()
    if is_reel is None:
        is_reel = f == "reel"
    post_type = kind_to_post_type(kind, is_reel)
    issues: list[str] = []

    if p == "instagram" and post_type == VIDEO_POST:
        issues.append("Instagram has no feed video — this publishes as a Reel (9:16); "
                      "audited as a Reel.")
        post_type = REEL
    if p == "tiktok" and post_type == IMAGE_POST:
        issues.append("TikTok has no single-image post — publish as a Photo Mode carousel "
                      "(4–35 images, 9:16).")
    if p == "tiktok" and post_type == VIDEO_POST and not is_reel:
        # TikTok video is one vertical format; a 'Post' video is fine, just 9:16.
        pass
    return post_type, issues
