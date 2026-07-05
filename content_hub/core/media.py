"""core.media — the image/video generation engine (the shared AI-image primitive).

Content-agnostic: it turns generic asset dicts into files, so every workflow
(social / blog / email) reuses it. Importable and safe to run inside an MCP
server: it never prints to stdout, never calls sys.exit, and
returns a structured receipt (raising only on programmer error). Human-readable
progress goes through an ``emit`` callback that defaults to stderr — stdout is
reserved for the MCP JSON-RPC channel.

  - images: gpt-image-2 (OpenAI Images API)
  - video:  veo-3.1-* (Google, async long-running operation)

Auth: OPENAI_API_KEY (images) and GEMINI_API_KEY (video) in the environment (see
config.load_dotenv). Credentials are NEVER read from an asset/manifest.
"""

from __future__ import annotations

import base64
import re
import struct
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# --- rough price table (USD), for the cost estimate only; verify against current
#     provider pricing. Video is per-second (Veo). ---------------------------------
VIDEO_PRICE_PER_SECOND = {
    "veo-3.1-generate-preview": 0.40,
    "veo-3.1-fast-generate-preview": 0.15,
    "veo-3.1-lite-generate-preview": 0.03,
}
DEFAULT_VIDEO_PRICE_PER_SECOND = 0.15

# Images (OpenAI gpt-image-2) bill per OUTPUT token ($/1M); the token count scales with
# size × quality. These are representative per-image estimates so the cost cell stays a
# useful guide — NOT a bill (see OpenAI's image-cost calculator for exact figures).
IMAGE_PRICE_PER_MTOK = 30.0
IMAGE_OUTPUT_TOKENS = {"low": 320, "medium": 1300, "high": 4600, "auto": 4600}
DEFAULT_IMAGE_QUALITY = "high"
DEFAULT_IMAGE_MODERATION = "low"


def image_est_cost(asset: dict | None = None) -> float:
    """Approx per-image cost from the requested quality (a guide, not a bill). Used for
    dry-run/mock and as the fallback when the API doesn't return usage."""
    q = (asset or {}).get("quality") or DEFAULT_IMAGE_QUALITY
    toks = IMAGE_OUTPUT_TOKENS.get(q, IMAGE_OUTPUT_TOKENS["high"])
    return round(toks * IMAGE_PRICE_PER_MTOK / 1_000_000, 4)


def image_actual_cost(usage) -> float | None:
    """The REAL per-image cost from the API's usage object (gpt-image-2 bills its output
    image tokens at $30/1M; the text-input term is a fraction of a cent, so we report the
    dominant output-token cost). None when usage is unavailable -> caller falls back to
    ``image_est_cost``."""
    out = getattr(usage, "output_tokens", None) if usage else None
    if not out:
        return None
    return round(out * IMAGE_PRICE_PER_MTOK / 1_000_000, 4)

# Veo 3.1: a base clip is up to 8s; longer clips are built by chaining "extend"
# calls that continue from the previous segment, ~7s each, up to VEO_MAX_SECONDS.
VEO_BASE_MAX_SECONDS = 8
VEO_EXTEND_SECONDS = 7
VEO_MAX_SECONDS = 30

DEFAULTS = {
    "image_model": "gpt-image-2",
    "video_model": "veo-3.1-generate-preview",
    "brand": "",
}

Emit = Callable[..., None]


def _stderr_emit(msg: str, *, err: bool = False) -> None:
    print(msg, file=sys.stderr)


# --- error taxonomy --------------------------------------------------------
FREE_TIER_HINT = ("This Google project is on the FREE tier, which allows 0 video "
                  "generations. Enable billing (paid tier): "
                  "https://ai.google.dev/gemini-api/docs/billing")
MODEL_HINT = ("Model not found or no access. Images: confirm your OpenAI account can use "
              "gpt-image-2 (org verification may be required). Video: Google rotates "
              "-preview builds — update DEFAULT_VIDEO_MODEL in core/config.py.")
AUTH_HINT = ("Check OPENAI_API_KEY (images) / GEMINI_API_KEY (video) in .env is valid "
             "and enabled.")
BILLING_HINT = ("OpenAI quota/billing: the image request was rejected for insufficient "
                "quota — add credit or check billing on the OpenAI account.")
MODERATION_HINT = ("The image was blocked by OpenAI content moderation. Revise the prompt "
                   "(moderation is already set to 'low').")
PER_DAY_HINT = ("This is a PER-DAY quota cap for the model (retrying won't clear it "
                "until the quota resets). Request a higher quota, use a lighter model "
                "(e.g. veo-3.1-fast-generate-preview), or run fewer per day. Tip: "
                "`--only image` finishes the images now; re-running skips what's done.")

_QUOTA_PERIOD_RE = re.compile(r"per\s*(day|minute)", re.IGNORECASE)
_RETRY_DELAY_RE = re.compile(r"retry\s*delay['\":\s]*?(\d+)\s*s", re.IGNORECASE)


def _quota_info(e: Exception) -> tuple[str | None, int | None]:
    """Pull (period, retry_delay_seconds) out of a 429 body when present.
    period is 'day' | 'minute' | None; retry delay is the API's suggested wait."""
    s = str(e)
    pm = _QUOTA_PERIOD_RE.search(s)
    dm = _RETRY_DELAY_RE.search(s)
    return (pm.group(1).lower() if pm else None, int(dm.group(1)) if dm else None)


def is_transient_error(e: Exception) -> bool:
    """True for retryable server-side hiccups (Veo throws internal 500s intermittently;
    OpenAI can throw connection errors / 5xx)."""
    s = str(e).lower()
    return any(k in s for k in (
        "internal", "'code': 13", "code: 13", "unavailable", "503", "500", "502", "504",
        "try again", "connection error", "timeout"))


# Hard caps / permanent failures that will NOT clear within a run, so we fail fast
# instead of burning the whole backoff schedule: Google free-tier (limit: 0), a PER-DAY
# quota, and OpenAI insufficient_quota / moderation blocks.
_NON_RETRYABLE = ("free_tier", "freetier", "limit: 0", "insufficient_quota",
                  "billing_hard_limit", "moderation_blocked")


def is_retryable_error(e: Exception) -> bool:
    """True if waiting and retrying could plausibly succeed: a transient 5xx/connection
    blip, or a per-minute rate-limit (429). NOT retryable: a hard quota/billing cap, a
    per-day quota, or a moderation block — none clear within a run."""
    if is_transient_error(e):
        return True
    s = str(e).lower()
    if any(k in s for k in _NON_RETRYABLE):
        return False
    if "resource_exhausted" in s or "429" in str(e) or "rate limit" in s or "rate_limit" in s:
        period, _ = _quota_info(e)
        return period != "day"
    return False


def friendly_error(e: Exception) -> tuple[str, str | None]:
    """Map a raw API exception to (short line, hint-or-None) so the log stays readable.
    Covers both the OpenAI image path and the Google video path."""
    msg = str(e)
    low = msg.lower()
    if "moderation_blocked" in low or "content_policy" in low or "safety system" in low:
        return ("image blocked by moderation", MODERATION_HINT)
    if "insufficient_quota" in low or "billing_hard_limit" in low:
        return ("OpenAI quota/billing", BILLING_HINT)
    if "resource_exhausted" in low or "429" in msg or "rate limit" in low:
        if "free_tier" in low or "freetier" in low or "limit: 0" in low:
            return ("429 quota: free-tier limit is 0 for these models", FREE_TIER_HINT)
        period, delay = _quota_info(e)
        scope = f"{period}" if period else "rate"
        tail = f"; API suggests retry in ~{delay}s" if delay else ""
        return (f"429 quota ({scope} limit){tail}",
                PER_DAY_HINT if period == "day" else None)
    if "not_found" in low or " 404" in msg or "does not exist" in low:
        return ("404 model not found / no access", MODEL_HINT)
    if any(s in low for s in ("permission_denied", "unauthenticated", "401", "403",
                              "api key", "incorrect api key")):
        return ("auth/permission error", AUTH_HINT)
    return (msg.splitlines()[0][:200], None)  # unknown: first line only


def validate_asset(asset: dict) -> str | None:
    """Pre-flight checks that catch known-invalid combos before spending. Runs in
    every mode (incl. dry-run), so a bad job fails fast."""
    if asset.get("type") == "video":
        res = asset.get("resolution", "720p")
        dur = int(asset.get("duration_seconds", 6))
        # Veo 1080p renders its base clip only at 8s; shorter is invalid. Longer
        # durations are fine — built by chaining 7s extensions onto the 8s base.
        if res == "1080p" and dur < 8:
            return f"Veo 1080p needs at least an 8s base (got {dur}); use 720p for shorter clips"
    return None


# --- naming ----------------------------------------------------------------
def revision_numbers(asset: dict) -> list[int]:
    """Which _vN suffixes to produce for this asset.

    'revisions' (alias: 'number_of_variations') = how many; 'revision_start' = first N.
    e.g. revisions=2 -> [1, 2]; revision_start=3, revisions=2 -> [3, 4]. Every output
    file is named <id>_v<N>.<ext> so it matches the Drive naming convention exactly.
    """
    count = int(asset.get("revisions", asset.get("number_of_variations", 1)))
    start = int(asset.get("revision_start", 1))
    return list(range(start, start + count))


def asset_target_dir(out_dir: Path, asset: dict) -> Path:
    """Where this asset's files go. An optional 'group' nests them in a subfolder
    (e.g. a carousel set), matching the 03_Carousels/<set>/ Drive structure."""
    return out_dir / asset["group"] if asset.get("group") else out_dir


def estimate_cost(assets: list[dict], defaults: dict | None = None) -> float:
    """Price-table estimate for a set of assets, without generating anything. Used to
    fill the cost cell for rows we skip (already on Drive) as well as dry-run totals."""
    defaults = defaults or DEFAULTS
    total = 0.0
    for a in assets:
        revs = len(revision_numbers(a))
        if a.get("type") == "video":
            model = a.get("model") or defaults["video_model"]
            price = VIDEO_PRICE_PER_SECOND.get(model, DEFAULT_VIDEO_PRICE_PER_SECOND)
            total += price * int(a.get("duration_seconds", 6)) * revs
        elif a.get("type") == "image":
            total += image_est_cost(a) * revs
    return round(total, 4)


# --- aspect ratios ----------------------------------------------------------
# gpt-image-2 accepts an arbitrary WIDTHxHEIGHT (both divisible by 16, aspect ratio
# between 1:3 and 3:1), so we render the desired ratio NATIVELY — no crop step. Sizes
# are ~1024px on the short edge; every value here is /16 and within the ratio bounds.
IMAGE_SIZES = {
    "1:1": "1024x1024",
    "4:5": "1024x1280",   # Instagram's tallest feed/carousel ratio
    "3:4": "1024x1344",
    "2:3": "1024x1536",
    "9:16": "1024x1792",
    "4:3": "1344x1024",
    "3:2": "1536x1024",
    "16:9": "1792x1024",
}
DEFAULT_IMAGE_SIZE = "1024x1024"


def image_size_for(aspect_ratio: str | None) -> str:
    """The gpt-image-2 ``size`` string for a desired aspect ratio (defaults to square)."""
    return IMAGE_SIZES.get((aspect_ratio or "").strip(), DEFAULT_IMAGE_SIZE)


# --- placeholder generators for mock mode (no deps, no API, no key) ---------
ASPECT_DIMS = {
    "1:1": (1024, 1024), "3:4": (768, 1024), "4:3": (1024, 768),
    "9:16": (576, 1024), "16:9": (1024, 576), "2:3": (768, 1152), "3:2": (1152, 768),
}


def _png_chunk(typ: bytes, data: bytes) -> bytes:
    body = typ + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_placeholder_png(path: Path, aspect_ratio: str | None, seed_text: str) -> None:
    """A valid solid-color PNG at the right aspect ratio, so you can eyeball shape.
    Colour is derived from the id so different assets look different."""
    w, h = ASPECT_DIMS.get(aspect_ratio or "1:1", (1024, 1024))
    hv = zlib.crc32(seed_text.encode())
    rgb = bytes((((hv >> 16) & 0x7F) + 0x40, ((hv >> 8) & 0x7F) + 0x40, (hv & 0x7F) + 0x40))
    row = b"\x00" + rgb * w          # filter byte + RGB pixels
    idat = zlib.compress(row * h, 6)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + _png_chunk(b"IHDR", ihdr)
                     + _png_chunk(b"IDAT", idat)
                     + _png_chunk(b"IEND", b""))


def write_placeholder_mp4(path: Path) -> None:
    """A tiny MP4-container stub (ftyp+free). Recognised as .mp4; NOT playable."""
    def box(typ: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data) + 8) + typ + data
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 0x200) + b"isomiso2mp41")
    free = box(b"free", b"placeholder - mock render, not a playable video")
    path.write_bytes(ftyp + free)


def _write_image_bytes(part_data, out_path: Path) -> None:
    """SDK inline_data.data is usually bytes; tolerate base64 str too."""
    if isinstance(part_data, str):
        part_data = base64.b64decode(part_data)
    out_path.write_bytes(part_data)


# --- generation ------------------------------------------------------------
def init_image_client():
    """Create the OpenAI client for image generation (gpt-image-2). Raises RuntimeError
    with an actionable message if the key or SDK is missing."""
    from . import config
    if not config.openai_api_key():
        raise RuntimeError("OPENAI_API_KEY not set. Add it to .env or the environment.")
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai not installed. Run: pip install -r requirements.txt") from e
    return OpenAI()


def init_video_client():
    """Create the google-genai client for video generation (Veo). Raises RuntimeError
    with an actionable message if the key or SDK is missing."""
    from . import config
    if not config.gemini_api_key():
        raise RuntimeError("GEMINI_API_KEY not set. Add it to .env or the environment.")
    try:
        from google import genai
        from google.genai import types as _types
    except ImportError as e:
        raise RuntimeError("google-genai not installed. Run: pip install -r requirements.txt") from e
    return genai.Client(), _types


def _post_process(out_path: Path, asset: dict) -> None:
    """After a slide is written: stamp on the exact overlay text (if any). gpt-image-2
    renders the requested ratio natively, so there's no crop step."""
    text = asset.get("overlay_text")
    if text:
        from . import config, textcard
        textcard.overlay_text(out_path, text, font_path=config.brand_font_path())


def generate_image(client, asset: dict, defaults: dict, out_dir: Path,
                   mode: str, emit: Emit, retries: int = 3, backoff: int = 15) -> list[dict]:
    model = asset.get("model") or defaults["image_model"]
    prompt = asset["prompt"]
    if asset.get("negative_prompt"):
        # gpt-image-2 has no negative-prompt field, so fold it into the prompt text.
        prompt = f"{prompt}. Avoid: {asset['negative_prompt']}."
    if defaults.get("brand"):
        prompt = f"{prompt} (brand: {defaults['brand']})"

    size = image_size_for(asset.get("aspect_ratio"))          # rendered natively, no crop
    quality = asset.get("quality") or DEFAULT_IMAGE_QUALITY
    moderation = asset.get("moderation") or DEFAULT_IMAGE_MODERATION
    est = image_est_cost(asset)
    target_dir = asset_target_dir(out_dir, asset)
    results = []
    for n in revision_numbers(asset):
        out_path = target_dir / f"{asset['id']}_v{n}.png"
        rel = out_path.relative_to(out_dir)
        if mode == "dry-run":
            emit(f"  [dry-run] image -> {rel}  (model={model}, {size}, q={quality})")
            results.append({"file": str(out_path), "model": model, "dry_run": True,
                            "est_cost_usd": est})
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "mock":
            write_placeholder_png(out_path, asset.get("aspect_ratio"), f"{asset['id']}_v{n}")
            _post_process(out_path, asset)
            emit(f"  mock   -> {rel}")
            results.append({"file": str(out_path), "model": model, "mock": True,
                            "est_cost_usd": est})
            continue

        # Retry rate-limit 429s and transient 5xx/connection blips with exponential
        # backoff; only a successful generation bills, so a retried call costs nothing.
        attempt = 0
        while True:
            try:
                resp = client.images.generate(
                    model=model, prompt=prompt, size=size,
                    quality=quality, moderation=moderation, n=1)
                # gpt-image models always return base64 (no url); guard an empty response.
                data = getattr(resp, "data", None) or []
                b64 = getattr(data[0], "b64_json", None) if data else None
                if not b64:
                    raise RuntimeError(f"no image data returned for asset '{asset['id']}'")
                _write_image_bytes(b64, out_path)   # tolerates base64 str
                _post_process(out_path, asset)
                break
            except Exception as e:
                if attempt < retries and is_retryable_error(e):
                    attempt += 1
                    wait = backoff * (2 ** (attempt - 1))
                    emit(f"  retry  -> {out_path.name}  ({friendly_error(e)[0]}; "
                         f"attempt {attempt}/{retries} after {wait}s)", err=True)
                    time.sleep(wait)
                    continue
                raise
        # Real billed cost from the API's usage; fall back to the estimate if absent.
        cost = image_actual_cost(getattr(resp, "usage", None)) or est
        emit(f"  image  -> {out_path.name}  (~${cost})" + (f"  (after {attempt} retr"
             f"{'y' if attempt == 1 else 'ies'})" if attempt else ""))
        results.append({"file": str(out_path), "model": model, "attempts": attempt + 1,
                        "est_cost_usd": cost})
    return results


def _submit_veo(client, types, *, model: str, prompt: str, config_kwargs: dict,
                video_input, poll_seconds: int, retries: int, backoff: int,
                emit: Emit, label: str):
    """Submit one Veo generate/extend call, poll to completion, retry transient/429.
    Returns (video_object, attempts). ``video_input`` is None for the base clip, or
    the previous segment's video for an extension."""
    attempt = 0
    while True:
        try:
            kwargs = {"model": model, "prompt": prompt,
                      "config": types.GenerateVideosConfig(**config_kwargs)}
            if video_input is not None:
                kwargs["video"] = video_input          # extension: continue this clip
            operation = client.models.generate_videos(**kwargs)
            emit(f"  video  -> {label}  (submitted, polling every {poll_seconds}s)")
            while not operation.done:
                time.sleep(poll_seconds)
                operation = client.operations.get(operation)
            if getattr(operation, "error", None):
                raise RuntimeError(f"Veo op failed for {label}: {operation.error}")
            return operation.response.generated_videos[0].video, attempt
        except Exception as e:
            if attempt < retries and is_retryable_error(e):
                attempt += 1
                wait = backoff * (2 ** (attempt - 1))
                emit(f"  retry  -> {label}  ({friendly_error(e)[0]}; "
                     f"attempt {attempt}/{retries} after {wait}s)", err=True)
                time.sleep(wait)
                continue
            raise


def generate_video(client, types, asset: dict, defaults: dict, out_dir: Path,
                   mode: str, emit: Emit, poll_seconds: int = 10,
                   retries: int = 2, backoff: int = 15) -> list[dict]:
    model = asset.get("model") or defaults["video_model"]
    duration = min(int(asset.get("duration_seconds", 6)), VEO_MAX_SECONDS)
    price = VIDEO_PRICE_PER_SECOND.get(model, DEFAULT_VIDEO_PRICE_PER_SECOND)
    est = round(price * duration, 3)

    target_dir = asset_target_dir(out_dir, asset)
    results = []
    for n in revision_numbers(asset):
        out_path = target_dir / f"{asset['id']}_v{n}.mp4"
        rel = out_path.relative_to(out_dir)
        if mode == "dry-run":
            emit(f"  [dry-run] video -> {rel}  (model={model}, {duration}s, ~${est})")
            results.append({"file": str(out_path), "model": model, "dry_run": True,
                            "est_cost_usd": est})
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "mock":
            write_placeholder_mp4(out_path)
            emit(f"  mock   -> {rel}  (stub .mp4, not playable)")
            results.append({"file": str(out_path), "model": model, "mock": True,
                            "est_cost_usd": est})
            continue

        # Shared config for every segment (aspect/resolution/negatives/seed must
        # match across the base clip and its extensions).
        common = {
            "aspect_ratio": asset.get("aspect_ratio", "16:9"),
            "resolution": asset.get("resolution", "720p"),
        }
        if asset.get("negative_prompt"):
            common["negative_prompt"] = asset["negative_prompt"]
        if asset.get("seed") is not None:
            common["seed"] = asset["seed"]

        # Base clip (<=8s), then chain ~7s extensions until we reach the target.
        base_len = min(VEO_BASE_MAX_SECONDS, duration)
        video, attempts = _submit_veo(
            client, types, model=model, prompt=asset["prompt"],
            config_kwargs={**common, "duration_seconds": base_len}, video_input=None,
            poll_seconds=poll_seconds, retries=retries, backoff=backoff, emit=emit,
            label=f"{out_path.name} (base {base_len}s)")
        current = base_len
        while current < duration:
            seg = min(VEO_EXTEND_SECONDS, duration - current)
            video, a2 = _submit_veo(
                client, types, model=model, prompt=asset["prompt"],
                config_kwargs={**common, "duration_seconds": seg}, video_input=video,
                poll_seconds=poll_seconds, retries=retries, backoff=backoff, emit=emit,
                label=f"{out_path.name} (extend +{seg}s -> {current + seg}s)")
            attempts += a2
            current += seg

        client.files.download(file=video)
        video.save(str(out_path))
        est = round(price * current, 3)  # bill by seconds actually produced
        emit(f"  video  -> {out_path.name}  (done {current}s, ~${est})")
        results.append({"file": str(out_path), "model": model, "est_cost_usd": est,
                        "seconds": current, "attempts": attempts + 1})
    return results


def run_batch(assets: list[dict], *, defaults: dict, out_dir: Path, mode: str,
              only: str | None = None, emit: Emit | None = None,
              poll_seconds: int = 10, retries: int = 3, backoff: int = 15,
              batch_id: str = "batch", image_client=None, video_client=None,
              types=None) -> dict:
    """Generate every asset in ``assets`` and return a receipt.

    ``mode`` is one of "dry-run" | "mock" | "live". A failed asset is recorded and
    skipped — one bad asset never kills the batch. Uploading is the caller's job
    (the Social workflow routes by type and checks Drive for existing files first).

    Images use the OpenAI client (``image_client``), video uses google-genai
    (``video_client``/``types``). For a live run the caller may pass pre-built clients
    (from init_image_client / init_video_client) to reuse them across many small
    batches; whichever is needed and not supplied is created lazily here.
    """
    if mode not in ("dry-run", "mock", "live"):
        raise ValueError(f"mode must be dry-run|mock|live, got {mode!r}")
    emit = emit or _stderr_emit

    if mode == "live":
        def _wanted(a):
            return a.get("type") in ("image", "video") and (not only or a.get("type") == only)
        if image_client is None and any(a.get("type") == "image" and _wanted(a) for a in assets):
            image_client = init_image_client()
        if video_client is None and any(a.get("type") == "video" and _wanted(a) for a in assets):
            video_client, types = init_video_client()

    receipt = {"batch_id": batch_id, "mode": mode,
               "generated_at": datetime.now(timezone.utc).isoformat(),
               "outputs": [], "errors": [], "hints": []}
    hints: set[str] = set()

    emit(f"Batch '{batch_id}' [{mode}] -> {out_dir}")
    for asset in assets:
        atype = asset.get("type")
        if only and atype != only:
            continue
        if atype not in ("image", "video"):
            receipt["errors"].append({"id": asset.get("id"), "error": f"bad type: {atype}",
                                      "reason": "bad type"})
            emit(f"  skip   -> {asset.get('id')} (bad type '{atype}')")
            continue
        verr = validate_asset(asset)
        if verr:
            receipt["errors"].append({"id": asset.get("id"), "error": verr,
                                      "reason": "invalid job"})
            emit(f"  skip   -> {asset.get('id')} ({verr})", err=True)
            continue
        try:
            if atype == "image":
                out = generate_image(image_client, asset, defaults, out_dir, mode, emit,
                                     retries, backoff)
            else:
                out = generate_video(video_client, types, asset, defaults, out_dir, mode,
                                     emit, poll_seconds, retries, backoff)
            receipt["outputs"].extend(out)
        except Exception as e:  # keep going; one bad asset shouldn't kill the batch
            short, hint = friendly_error(e)
            receipt["errors"].append({"id": asset.get("id"), "error": str(e), "reason": short})
            if hint:
                hints.add(hint)
            emit(f"  FAILED -> {asset.get('id')}: {short}", err=True)

    receipt["estimated_cost_usd"] = round(
        sum(o.get("est_cost_usd", 0) for o in receipt["outputs"]), 2)
    receipt["hints"] = sorted(hints)
    return receipt
