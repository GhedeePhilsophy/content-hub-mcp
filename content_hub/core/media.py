"""core.media — the image/video generation engine (the shared AI-image primitive).

Content-agnostic: it turns generic asset dicts into files, so every workflow
(social / blog / email) reuses it. Importable and safe to run inside an MCP
server: it never prints to stdout, never calls sys.exit, and
returns a structured receipt (raising only on programmer error). Human-readable
progress goes through an ``emit`` callback that defaults to stderr — stdout is
reserved for the MCP JSON-RPC channel.

  - images: gemini-2.5-flash-image ("Nano Banana")
  - video:  veo-3.1-* (async long-running operation)

Auth: GEMINI_API_KEY in the environment (see config.load_dotenv). Credentials
are NEVER read from an asset/manifest.
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
#     Google pricing. Images are flat-per-image; video is per-second. ---------------
IMAGE_PRICE_PER_IMAGE = 0.039  # gemini-2.5-flash-image, ~1K output
VIDEO_PRICE_PER_SECOND = {
    "veo-3.1-generate-preview": 0.40,
    "veo-3.1-fast-generate-preview": 0.15,
    "veo-3.1-lite-generate-preview": 0.03,
}
DEFAULT_VIDEO_PRICE_PER_SECOND = 0.15

# Veo 3.1: a base clip is up to 8s; longer clips are built by chaining "extend"
# calls that continue from the previous segment, ~7s each, up to VEO_MAX_SECONDS.
VEO_BASE_MAX_SECONDS = 8
VEO_EXTEND_SECONDS = 7
VEO_MAX_SECONDS = 30

DEFAULTS = {
    "image_model": "gemini-2.5-flash-image",
    "video_model": "veo-3.1-generate-preview",
    "brand": "",
}

Emit = Callable[..., None]


def _stderr_emit(msg: str, *, err: bool = False) -> None:
    print(msg, file=sys.stderr)


# --- error taxonomy --------------------------------------------------------
FREE_TIER_HINT = ("This API key's project is on the FREE tier, which allows 0 image/video "
                  "generations. Enable billing (paid tier): "
                  "https://ai.google.dev/gemini-api/docs/billing")
STALE_MODEL_HINT = ("A model name looks stale — Google rotates -preview builds. Update "
                    "defaults.image_model / video_model from "
                    "https://ai.google.dev/gemini-api/docs")
AUTH_HINT = "Check GEMINI_API_KEY in .env is valid and enabled for this project."
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
    """True for retryable server-side hiccups (Veo throws internal 500s intermittently)."""
    s = str(e).lower()
    return any(k in s for k in (
        "internal", "'code': 13", "code: 13", "unavailable", "503", "500", "try again"))


def is_retryable_error(e: Exception) -> bool:
    """True if waiting and retrying could plausibly succeed: a transient 5xx, or a
    per-minute 429 rate-limit. NOT retryable: the free-tier hard cap (limit: 0) or a
    PER-DAY quota — neither clears within a run, so we fail fast instead of burning
    the full backoff schedule on every affected row."""
    if is_transient_error(e):
        return True
    s = str(e).lower()
    if "resource_exhausted" in s or "429" in str(e):
        if "free_tier" in s or "freetier" in s or "limit: 0" in s:
            return False
        period, _ = _quota_info(e)
        return period != "day"
    return False


def friendly_error(e: Exception) -> tuple[str, str | None]:
    """Map a raw API exception to (short line, hint-or-None) so the log stays readable."""
    msg = str(e)
    low = msg.lower()
    if "resource_exhausted" in low or "429" in msg:
        if "free_tier" in low or "freetier" in low or "limit: 0" in low:
            return ("429 quota: free-tier limit is 0 for these models", FREE_TIER_HINT)
        period, delay = _quota_info(e)
        scope = f"{period}" if period else "rate"
        tail = f"; API suggests retry in ~{delay}s" if delay else ""
        return (f"429 quota ({scope} limit){tail}",
                PER_DAY_HINT if period == "day" else None)
    if "not_found" in low or " 404" in msg:
        return ("404 model not found", STALE_MODEL_HINT)
    if any(s in low for s in ("permission_denied", "unauthenticated", "401", "403", "api key")):
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
            total += IMAGE_PRICE_PER_IMAGE * revs
    return round(total, 4)


# --- aspect ratios ----------------------------------------------------------
# What gemini-2.5-flash-image accepts directly (per the SDK's ImageConfig docs).
SUPPORTED_IMAGE_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"}
# Desired ratios the model can't render -> (generate at this ratio, then center-crop
# to the desired one). 4:5 (Instagram's tallest) is cropped from a 3:4 render.
_CROP_SOURCE = {"4:5": "3:4", "5:4": "4:3"}


def resolve_ratio(desired: str | None) -> tuple[str | None, str | None]:
    """Return (generation_ratio, crop_to). crop_to is None when the model can render
    the desired ratio directly; otherwise we generate wider/taller and crop."""
    if not desired or desired in SUPPORTED_IMAGE_RATIOS:
        return desired, None
    src = _CROP_SOURCE.get(desired)
    return (src, desired) if src else (desired, None)


def _center_crop_file(path: Path, ratio: str) -> None:
    """Center-crop the image at ``path`` to ``ratio`` (e.g. '4:5'), in place."""
    from PIL import Image
    tw, th = (int(x) for x in ratio.split(":"))
    target = tw / th
    img = Image.open(path).convert("RGB")
    w, h = img.size
    if abs(w / h - target) < 0.01:
        return
    if w / h > target:                       # too wide -> trim width
        nw = max(1, round(h * target)); x = (w - nw) // 2
        img = img.crop((x, 0, x + nw, h))
    else:                                    # too tall -> trim height
        nh = max(1, round(w / target)); y = (h - nh) // 2
        img = img.crop((0, y, w, y + nh))
    img.save(path)


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
def init_live_client():
    """Create the google-genai client for a live run. Raises RuntimeError with an
    actionable message if the key or SDK is missing."""
    from . import config
    if not config.gemini_api_key():
        raise RuntimeError("GEMINI_API_KEY not set. Add it to .env or the environment.")
    try:
        from google import genai
        from google.genai import types as _types
    except ImportError as e:
        raise RuntimeError("google-genai not installed. Run: pip install -r requirements.txt") from e
    return genai.Client(), _types


def _post_process(out_path: Path, asset: dict, crop_to: str | None) -> None:
    """After a slide is written: crop to the desired ratio (if the model couldn't
    render it directly), then stamp on the exact overlay text."""
    if crop_to:
        _center_crop_file(out_path, crop_to)
    text = asset.get("overlay_text")
    if text:
        from . import config, textcard
        textcard.overlay_text(out_path, text, font_path=config.brand_font_path())


def generate_image(client, types, asset: dict, defaults: dict, out_dir: Path,
                   mode: str, emit: Emit, retries: int = 3, backoff: int = 15) -> list[dict]:
    model = asset.get("model") or defaults["image_model"]
    prompt = asset["prompt"]
    if asset.get("negative_prompt"):
        prompt = f"{prompt}. Avoid: {asset['negative_prompt']}."
    if defaults.get("brand"):
        prompt = f"{prompt} (brand: {defaults['brand']})"

    # 4:5 (and any unsupported ratio) is rendered at a supported ratio then cropped.
    gen_ratio, crop_to = resolve_ratio(asset.get("aspect_ratio"))
    target_dir = asset_target_dir(out_dir, asset)
    results = []
    for n in revision_numbers(asset):
        out_path = target_dir / f"{asset['id']}_v{n}.png"
        rel = out_path.relative_to(out_dir)
        if mode == "dry-run":
            emit(f"  [dry-run] image -> {rel}  (model={model})")
            results.append({"file": str(out_path), "model": model, "dry_run": True,
                            "est_cost_usd": round(IMAGE_PRICE_PER_IMAGE, 4)})
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "mock":
            write_placeholder_png(out_path, gen_ratio, f"{asset['id']}_v{n}")
            _post_process(out_path, asset, crop_to)
            emit(f"  mock   -> {rel}")
            results.append({"file": str(out_path), "model": model, "mock": True,
                            "est_cost_usd": round(IMAGE_PRICE_PER_IMAGE, 4)})
            continue

        config_obj = None
        if gen_ratio:
            # Nano Banana takes aspect ratio via image_config.
            config_obj = types.GenerateContentConfig(
                response_modalities=["Image"],
                image_config=types.ImageConfig(aspect_ratio=gen_ratio),
            )
        # Retry rate-limit 429s and transient 5xx with exponential backoff; only a
        # successful generation bills, so a retried call costs nothing extra.
        attempt = 0
        while True:
            try:
                resp = client.models.generate_content(
                    model=model, contents=prompt, config=config_obj)
                # A safety-blocked or empty response has candidates[0].content == None
                # (or no candidates), so guard every hop instead of crashing on .parts.
                cand = (resp.candidates or [None])[0]
                content = getattr(cand, "content", None) if cand else None
                parts = getattr(content, "parts", None) if content else None
                if not parts:
                    reason = getattr(cand, "finish_reason", None) if cand else None
                    block = getattr(getattr(resp, "prompt_feedback", None),
                                    "block_reason", None)
                    detail = reason or block
                    raise RuntimeError(
                        f"no content returned for '{asset['id']}'"
                        + (f" (reason={detail})" if detail else "")
                        + " — usually a safety block; revise the prompt")
                saved = False
                for part in parts:
                    if getattr(part, "inline_data", None) and part.inline_data.data:
                        _write_image_bytes(part.inline_data.data, out_path)
                        saved = True
                        break
                if not saved:
                    raise RuntimeError(f"no image data returned for asset '{asset['id']}'")
                _post_process(out_path, asset, crop_to)
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
        emit(f"  image  -> {out_path.name}" + (f"  (after {attempt} retr"
             f"{'y' if attempt == 1 else 'ies'})" if attempt else ""))
        results.append({"file": str(out_path), "model": model, "attempts": attempt + 1,
                        "est_cost_usd": round(IMAGE_PRICE_PER_IMAGE, 4)})
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
              batch_id: str = "batch", client=None, types=None) -> dict:
    """Generate every asset in ``assets`` and return a receipt.

    ``mode`` is one of "dry-run" | "mock" | "live". A failed asset is recorded and
    skipped — one bad asset never kills the batch. Uploading is the caller's job
    (the Social workflow routes by type and checks Drive for existing files first).

    For a live run the caller may pass a pre-built ``client``/``types`` (from
    init_live_client) to reuse one API client across many small batches.
    """
    if mode not in ("dry-run", "mock", "live"):
        raise ValueError(f"mode must be dry-run|mock|live, got {mode!r}")
    emit = emit or _stderr_emit

    if mode == "live" and client is None:
        client, types = init_live_client()

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
                out = generate_image(client, types, asset, defaults, out_dir, mode, emit,
                                     retries, backoff)
            else:
                out = generate_video(client, types, asset, defaults, out_dir, mode, emit,
                                     poll_seconds, retries, backoff)
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
