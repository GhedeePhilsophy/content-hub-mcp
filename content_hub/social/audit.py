"""social.audit — compliance audit of a calendar's posts vs. the canonical standards.

Answers, per post: *is this delivered asset and this caption fit for where it's going to
be published?* Each row is classified to a platform × post-type (see [specs]), then its
real Drive asset is downloaded and measured — **aspect ratio, resolution, file size** — and
its caption checked for **length / hashtag count / fold** against that platform's limits.
Findings are returned as a structured report and (in ``live`` mode) written back into the
sheet's ``Audit Results`` column. Read-only w.r.t. assets: nothing is generated, moved,
deleted, re-permissioned, or billed.

Modes (same three-mode contract as the rest of the workflow):
  dry-run  classify + caption checks only; assets are NOT downloaded and nothing is written.
  mock     full read-only inspection (downloads + measures) but does NOT write to the sheet.
  live     full inspection AND writes the per-row verdict into Audit Results.

This module owns only pure measurement + orchestration; the standards live in [specs] so
generation and the audit can never disagree. stdout is never touched (MCP channel) —
progress goes through ``emit`` (stderr); the function returns a structured dict.
"""

from __future__ import annotations

import io
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date

from . import specs, sheet_ops
from ..core import config
from ..core.drive import FOLDER_MIME, file_id_from_link

# verdict severity ordering (worst wins for a row's overall verdict)
_SEVERITY = {"NA": 0, "PASS": 1, "WARN": 2, "FAIL": 3}

# aspect-ratio labels the audit recognises -> their decimal (w/h) value.
KNOWN_RATIOS = {
    "1:1": 1.0, "4:5": 0.8, "5:4": 1.25, "9:16": 0.5625, "16:9": 16 / 9,
    "1.91:1": 1.91, "2:3": 2 / 3, "3:2": 1.5, "3:4": 0.75, "4:3": 4 / 3,
}
# Fractional tolerance when matching a measured ratio to an accepted one (~4%). Covers
# gpt-image-2's /16 rounding (e.g. 1024x1280 = 0.800, 1080x1350 = 0.800) and minor crops.
ASPECT_TOLERANCE = 0.04


def _stderr(msg: str, **_k) -> None:
    print(msg, file=sys.stderr)


# --- pure helpers (property-tested) ----------------------------------------
def parse_aspect(label: str) -> float | None:
    """'9:16' / '1.91:1' -> decimal w/h, or None if unparseable."""
    try:
        w, h = label.split(":")
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return None


def measured_aspect_label(w: int, h: int) -> str:
    """Nearest recognised ratio label for pixel dims, or a reduced 'w:h' if none is close."""
    if not w or not h:
        return "?"
    val = w / h
    best, bestdiff = None, None
    for label, rv in KNOWN_RATIOS.items():
        diff = abs(val - rv) / rv
        if bestdiff is None or diff < bestdiff:
            best, bestdiff = label, diff
    if bestdiff is not None and bestdiff <= ASPECT_TOLERANCE:
        return best
    from math import gcd
    g = gcd(w, h) or 1
    return f"{w // g}:{h // g}"


def aspect_matches(w: int, h: int, accepted: tuple[str, ...],
                   tol: float = ASPECT_TOLERANCE) -> bool:
    """True if pixel dims fall within ``tol`` of ANY accepted ratio."""
    if not w or not h:
        return False
    val = w / h
    for label in accepted:
        rv = parse_aspect(label)
        if rv and abs(val - rv) / rv <= tol:
            return True
    return False


_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")


def count_hashtags(*texts: str | None) -> int:
    """Total '#tag' tokens across one or more text fields."""
    return sum(len(_HASHTAG_RE.findall(t or "")) for t in texts)


# --- check + row models ----------------------------------------------------
@dataclass
class Check:
    name: str          # aspect | resolution | file_size | caption_length | hashtags | slides | format
    verdict: str       # PASS | WARN | FAIL | NA
    detail: str


@dataclass
class RowAudit:
    row_id: str
    platform: str
    post_type: str
    fmt: str
    status: str
    overall: str = "NA"
    checks: list[Check] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)   # platform/format validity notes
    measured: dict = field(default_factory=dict)      # raw dims/size/etc. for transparency

    def add(self, name: str, verdict: str, detail: str) -> None:
        self.checks.append(Check(name, verdict, detail))

    def finalize(self) -> None:
        ranked = [c.verdict for c in self.checks] or ["NA"]
        self.overall = max(ranked, key=lambda v: _SEVERITY[v])


# --- caption checks (no I/O) -----------------------------------------------
def audit_caption(row: RowAudit, caption: str, hashtags_field: str,
                  cspec: specs.CaptionSpec) -> None:
    text = caption or ""
    n = len(text)
    if not text.strip():
        row.add("caption_length", "WARN", "caption is empty")
    elif n > cspec.caption_max:
        row.add("caption_length", "FAIL",
                f"{n} chars exceeds the {cspec.caption_max} cap")
    else:
        row.add("caption_length", "PASS",
                f"{n}/{cspec.caption_max} chars (first {cspec.fold} show before '…more')")
    row.measured["fold_preview"] = text[:cspec.fold]

    tags = count_hashtags(text, hashtags_field)
    row.measured["hashtags"] = tags
    if cspec.hashtag_max is not None and tags > cspec.hashtag_max:
        row.add("hashtags", "WARN", f"{tags} hashtags exceeds the {cspec.hashtag_max} max")
    elif tags:
        row.add("hashtags", "PASS", f"{tags} hashtags")


# --- asset measurement (I/O) -----------------------------------------------
def _image_dims(raw: bytes) -> tuple[int, int] | None:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            return im.size  # (w, h)
    except Exception:
        return None


def _video_dims(raw: bytes) -> tuple[int, int] | None:
    """(w, h) of an mp4 via imageio+ffmpeg; None if unreadable."""
    import os
    import tempfile
    tmp = None
    try:
        import imageio
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.write(raw)
        tmp.close()
        reader = imageio.get_reader(tmp.name)
        meta = reader.get_meta_data() or {}
        size = meta.get("size")
        if not size:
            frame = reader.get_data(0)
            size = (frame.shape[1], frame.shape[0])  # (w, h) from (h, w, c)
        reader.close()
        return (int(size[0]), int(size[1]))
    except Exception:
        return None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def _size_mb(meta: dict) -> float | None:
    try:
        return round(int(meta["size"]) / (1024 * 1024), 2)
    except (KeyError, TypeError, ValueError):
        return None


def _check_media(row: RowAudit, w: int | None, h: int | None, size_mb: float | None,
                 spec: specs.MediaSpec, *, prefix: str = "") -> None:
    """Aspect + resolution + file-size checks for one measured file."""
    if w and h:
        label = measured_aspect_label(w, h)
        row.measured[f"{prefix}dims"] = f"{w}x{h}"
        if aspect_matches(w, h, spec.accepted_aspects):
            row.add(f"{prefix}aspect", "PASS", f"{label} ({w}x{h})")
        else:
            row.add(f"{prefix}aspect", "FAIL",
                    f"{label} ({w}x{h}) not in accepted {list(spec.accepted_aspects)}")
        short_edge = min(w, h)
        if short_edge < spec.min_short_edge_px:
            row.add(f"{prefix}resolution", "WARN",
                    f"short edge {short_edge}px below the {spec.min_short_edge_px}px minimum")
        else:
            row.add(f"{prefix}resolution", "PASS", f"short edge {short_edge}px")
    else:
        row.add(f"{prefix}aspect", "NA", "could not read image/video dimensions")
    if size_mb is not None:
        row.measured[f"{prefix}size_mb"] = size_mb
        if size_mb > spec.max_file_mb:
            row.add(f"{prefix}file_size", "FAIL",
                    f"{size_mb} MB exceeds the {spec.max_file_mb} MB cap")
        else:
            row.add(f"{prefix}file_size", "PASS", f"{size_mb} MB")


def _audit_asset(drive, row: RowAudit, job, spec: specs.MediaSpec, emit) -> None:
    """Download + measure the row's real asset(s). Any resolution failure is reported,
    never raised (one bad asset never kills the run)."""
    link = job.existing_link if isinstance(job.existing_link, str) else None
    if link and link.strip().lower() == "failed":
        row.add("asset", "NA", "generation marked Failed — nothing to audit")
        return
    if not (link and link.startswith("http")):
        link = job.selected_link
    if not (isinstance(link, str) and link.startswith("http")):
        if job.plan.recorded:
            row.add("asset", "NA", "recorded clip — awaiting footage")
        else:
            row.add("asset", "NA", "no asset link on the row")
        return
    fid = file_id_from_link(link)
    if not fid:
        row.add("asset", "NA", "asset link is not a parseable Drive URL")
        return

    try:
        if job.plan.kind == "carousel":
            _audit_carousel(drive, row, fid, spec, emit)
            return
        meta = drive.get_file(fid, fields="id,name,mimeType,size")
        if meta.get("mimeType") == FOLDER_MIME:
            _audit_carousel(drive, row, fid, spec, emit)
            return
        raw = drive.download_bytes(fid)
        is_video = job.plan.kind == "video" or meta.get("mimeType", "").startswith("video/")
        dims = _video_dims(raw) if is_video else _image_dims(raw)
        w, h = dims if dims else (None, None)
        _check_media(row, w, h, _size_mb(meta), spec)
    except Exception as e:  # network/permission/decode — report, don't crash the batch
        row.add("asset", "NA", f"could not fetch/measure asset: {str(e).splitlines()[0][:120]}")


def _audit_carousel(drive, row: RowAudit, folder_id: str, spec: specs.MediaSpec, emit) -> None:
    slides = sorted(
        (f for f in drive.list_children(folder_id)
         if str(f.get("mimeType", "")).startswith("image/")),
        key=lambda f: f["name"])
    n = len(slides)
    row.measured["slides"] = n
    lo, hi = spec.carousel_min_slides, spec.carousel_max_slides
    if n == 0:
        row.add("slides", "NA", "carousel folder holds no images")
        return
    if (lo and n < lo) or (hi and n > hi):
        row.add("slides", "WARN",
                f"{n} slides outside the {lo}–{hi} range for this platform")
    else:
        row.add("slides", "PASS", f"{n} slides")
    # measure every slide; fold each check into one worst-case verdict for the set
    worst = {"aspect": "PASS", "resolution": "PASS", "file_size": "PASS"}
    detail = {}
    for f in slides:
        raw = drive.download_bytes(f["id"])
        dims = _image_dims(raw)
        w, h = dims if dims else (None, None)
        sub = RowAudit(row.row_id, row.platform, row.post_type, row.fmt, row.status)
        _check_media(sub, w, h, _size_mb(f), spec)
        for c in sub.checks:
            base = c.name  # aspect|resolution|file_size
            if _SEVERITY[c.verdict] > _SEVERITY[worst.get(base, "NA")]:
                worst[base] = c.verdict
                detail[base] = f"slide {f['name']}: {c.detail}"
    for base, verdict in worst.items():
        row.add(base, verdict, detail.get(base, f"all {n} slides OK"))


# --- orchestration ---------------------------------------------------------
def _summary_line(row: RowAudit) -> str:
    """The concise per-row string written into Audit Results."""
    today = date.today().isoformat()
    if row.overall == "PASS":
        return f"audit {today}: pass"
    if row.overall == "NA":
        return f"audit {today}: not audited ({row.checks[0].detail if row.checks else 'no checks'})"
    fails = [c.detail for c in row.checks if c.verdict == "FAIL"]
    warns = [c.detail for c in row.checks if c.verdict == "WARN"]
    parts = []
    if fails:
        parts.append("FAIL — " + "; ".join(fails))
    if warns:
        parts.append("WARN — " + "; ".join(warns))
    line = f"audit {today}: " + " | ".join(parts)
    return line[:490]  # keep the cell tidy


def audit_calendar(calendar_id: str, *, mode: str = "dry-run",
                   statuses: tuple[str, ...] | None = None, emit=None) -> dict:
    """Audit a calendar's posts against the canonical per-platform specs.

    ``statuses`` optionally restricts which rows are audited (e.g. ('Approved',) to check a
    round before export); default None audits every row with a Row ID. Returns a structured
    report; in ``live`` mode also writes each row's verdict into Audit Results.
    """
    emit = emit or _stderr
    if mode not in ("dry-run", "mock", "live"):
        raise ValueError(f"mode must be dry-run|mock|live, got {mode!r}")
    download = mode in ("mock", "live")
    write = mode == "live"

    from . import edit_ops
    drive, sid, tab, cal, sheet_link = edit_ops._load_live(calendar_id)
    rev_col = cal.cols.get("audit_results")

    rows: list[RowAudit] = []
    writebacks: list[tuple[str, object]] = []
    jobs = [j for j in cal.read_jobs() if j.row_id]
    emit(f"audit: {calendar_id} [{mode}] — {len(jobs)} rows"
         + (f", statuses={list(statuses)}" if statuses else ""))

    for job in jobs:
        if statuses and job.status not in statuses:
            continue
        kind = job.plan.kind
        is_reel = (job.fmt or "").strip().lower() == "reel"
        post_type, issues = specs.classify(job.platform, job.fmt, kind, is_reel) \
            if kind in ("image", "video", "carousel") else (kind, [])
        row = RowAudit(job.row_id, job.platform, post_type, job.fmt, job.status, issues=issues)

        # caption checks (always; free)
        audit_caption(row, job.caption, job.hashtags, specs.caption_spec(job.platform))

        # asset checks
        if kind not in ("image", "video", "carousel"):
            row.add("asset", "NA", job.plan.reason or f"visual type not audited ({kind})")
        elif not download:
            row.add("asset", "NA", "not inspected in dry-run (use --mode mock or live)")
        else:
            spec = specs.media_spec(job.platform, post_type)
            _audit_asset(drive, row, job, spec, emit)

        row.finalize()
        rows.append(row)
        if write and rev_col:
            writebacks.append((_a1(tab, rev_col, job.row_index), _summary_line(row)))

    # write-back (live only)
    updated = 0
    if write:
        if not rev_col:
            emit("audit: no 'Audit Results' column on the sheet — findings not written back",
                 err=True)
        elif writebacks:
            from ..core.sheets import SheetsClient
            sheets = SheetsClient(config.credentials_path(), config.token_path())
            res = sheets.batch_update(sid, writebacks)
            updated = res.get("totalUpdatedCells", len(writebacks))
            emit(f"audit: wrote {updated} verdict(s) to Audit Results")

    tally = {"pass": 0, "warn": 0, "fail": 0, "na": 0}
    for r in rows:
        tally[{"PASS": "pass", "WARN": "warn", "FAIL": "fail", "NA": "na"}[r.overall]] += 1
    emit(f"audit: {tally['fail']} fail / {tally['warn']} warn / {tally['pass']} pass "
         f"/ {tally['na']} n/a")

    return {
        "calendar_id": calendar_id,
        "mode": mode,
        "audited": len(rows),
        "summary": tally,
        "sheet_link": sheet_link,
        "wrote_back": updated if write else 0,
        "specs_verified": specs.VERIFIED,
        "rows": [_row_dict(r) for r in rows],
        "note": {
            "dry-run": "dry-run: assets not downloaded, sheet not written.",
            "mock": "mock: assets inspected read-only; sheet not written.",
            "live": "live: assets inspected; verdicts written to Audit Results.",
        }[mode],
    }


def _a1(tab: str, col: int, row: int) -> str:
    from openpyxl.utils import get_column_letter
    return f"'{tab}'!{get_column_letter(col)}{row}"


def _row_dict(r: RowAudit) -> dict:
    d = asdict(r)
    d["checks"] = [{"name": c["name"], "verdict": c["verdict"], "detail": c["detail"]}
                   for c in d["checks"]]
    return d
