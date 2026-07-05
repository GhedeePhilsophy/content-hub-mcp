"""social.calendar — the Social Calendar spreadsheet is the source of truth.

Reads the local working copy of Ghedee_Social_Calendar_<id>_v<n>.xlsx, turns each
in-scope Draft row into media-generation job(s), and (after generation + upload)
writes the Drive link and cost back into that row as a real hyperlink.

Columns are located by header name (row 1), not fixed index, so adding the new
'Slides' column — or any reordering — doesn't break the reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import rules
from ..core import config

# Carousel prompt parsing: split "Slide N — <desc>. On-image text: "<text>"" segments
# and the trailing global Style/Palette clause that applies to every slide.
_SLIDE_MARK = re.compile(r"Slide\s+\d+\s*[—–\-]\s*", re.IGNORECASE)
_STYLE_TAIL = re.compile(r"\b(?:Style|Palette)\s*:", re.IGNORECASE)
_ONIMG = re.compile(r"On-image text\s*:", re.IGNORECASE)
_QUOTES = "“”\"'‘’"

# Carousel slides are painted clean and the wording is stamped on afterward, so the
# MODEL must render none. gemini-2.5-flash-image tends to invent titles/subtitles, so
# this is deliberately forceful and repeated in both the prompt and the negatives.
_NO_TEXT_DIRECTIVE = (
    " CRITICAL: This image must contain absolutely NO text of any kind — no words, "
    "letters, captions, titles, subtitles, labels, numbers, signage, handwriting or "
    "typography anywhere in the frame. Render only the scene. Keep the lower third as "
    "clean, unobstructed negative space (text is added separately afterward).")
_NO_TEXT_NEGATIVE = (", any text, words, letters, captions, titles, subtitles, labels, "
                     "numbers, signage, handwriting, typography, watermark")


def _balance_quotes(text: str | None) -> str | None:
    """Append matching closing curly quotes for any unmatched opening ones, so an
    overlay like  The voice that calls you ‘not enough.  reads  …‘not enough.’"""
    if not text:
        return text
    for open_c, close_c in (("‘", "’"), ("“", "”")):
        diff = text.count(open_c) - text.count(close_c)
        if diff > 0:
            text += close_c * diff
    return text


def parse_carousel_prompt(prompt: str) -> list[dict]:
    """Split a multi-slide carousel prompt into per-slide {desc, text}.

    ``desc`` is the slide's visual direction with the global Style/Palette clause
    appended and the on-image-text directive removed (so the model paints clean
    artwork); ``text`` is the exact wording to overlay, or None. Returns [] if the
    prompt has no ``Slide N`` markers (caller falls back to the whole prompt)."""
    prompt = prompt or ""
    mtail = _STYLE_TAIL.search(prompt)
    body = prompt[:mtail.start()] if mtail else prompt
    tail = prompt[mtail.start():].strip() if mtail else ""

    marks = list(_SLIDE_MARK.finditer(body))
    slides = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        seg = body[m.end():end].strip()
        om = _ONIMG.search(seg)
        if om:
            desc = seg[:om.start()].strip()
            text = _balance_quotes(seg[om.end():].strip().strip(_QUOTES).strip()) or None
        else:
            desc, text = seg, None
        slides.append({"desc": (f"{desc} {tail}".strip() if tail else desc), "text": text})
    return slides

# canonical field -> accepted header spellings (normalised: lower, single-spaced)
_HEADER_ALIASES = {
    "row_id": {"row id"},
    "date": {"date"},
    "day": {"day"},
    "time": {"time (et)", "time"},
    "platform": {"platform"},
    "pillar": {"content pillar", "pillar"},
    "format": {"format"},
    "hook": {"hook / headline", "hook/headline", "hook", "headline"},
    "caption": {"caption (full copy)", "caption"},
    "hashtags": {"first-comment hashtags (ig)", "first-comment hashtags", "hashtags"},
    "visual_type": {"visual type"},
    "prompt": {"visual direction / prompt(s)", "visual direction / prompt",
               "visual direction / prompts", "visual direction"},
    "ai_model": {"ai model"},
    "est_cost": {"est. cost (usd)", "est cost (usd)", "est. cost", "est cost"},
    "asset_link": {"generated asset link (drive)", "generated asset link"},
    "status": {"status"},
    "notes": {"your notes", "notes"},
    "slides": {"slides", "slide count", "# slides"},
    "carousel_group": {"carousel group", "group"},
}

LINK_FONT_COLOR = "0563C1"  # Excel's default hyperlink blue


def _norm(s) -> str:
    return " ".join(str(s or "").strip().lower().split())


@dataclass
class RowJob:
    """One calendar row's generation plan."""
    row_index: int                 # 1-based worksheet row
    row_id: str
    platform: str
    pillar: str
    hook: str
    status: str
    visual_type: str
    fmt: str
    prompt: str
    model: str
    plan: rules.VisualPlan
    assets: list[dict] = field(default_factory=list)  # core.media asset dicts
    group: str | None = None       # carousel group folder name
    existing_link: str | None = None
    skip_reason: str = ""          # non-empty => don't generate (status/visual/etc.)
    # display fields (for the review preview; not used by generation)
    date: str = ""
    day: str = ""
    caption: str = ""
    hashtags: str = ""
    notes: str = ""

    @property
    def in_scope(self) -> bool:
        return not self.skip_reason


class Calendar:
    """A loaded calendar workbook + its header map. Read jobs, then write links back."""

    def __init__(self, source):
        """``source`` is a filesystem path, or a file-like object / BytesIO (e.g. an
        xlsx downloaded from Drive for a read-only preview). ``path`` is None for the
        latter, so save() is a no-op target and must be given an explicit path."""
        import openpyxl
        if isinstance(source, (str, Path)):
            self.path = Path(source)
            self.wb = openpyxl.load_workbook(self.path)
        else:
            self.path = None
            self.wb = openpyxl.load_workbook(source)
        self.ws = self._pick_sheet()
        self.cols = self._map_headers()

    def _pick_sheet(self):
        for ws in self.wb.worksheets:
            if "calendar" in ws.title.lower():
                return ws
        return self.wb.worksheets[0]

    def _map_headers(self) -> dict[str, int]:
        header_to_field = {h: fld for fld, hs in _HEADER_ALIASES.items() for h in hs}
        cols: dict[str, int] = {}
        for c in range(1, self.ws.max_column + 1):
            fld = header_to_field.get(_norm(self.ws.cell(1, c).value))
            if fld and fld not in cols:
                cols[fld] = c
        missing = {"row_id", "visual_type", "prompt", "status", "asset_link"} - cols.keys()
        if missing:
            raise ValueError(f"calendar is missing required column(s): {sorted(missing)}")
        return cols

    def _get(self, row: int, field_name: str):
        c = self.cols.get(field_name)
        return self.ws.cell(row, c).value if c else None

    @staticmethod
    def _fmt_date(v) -> str:
        """Date cell -> 'YYYY-MM-DD' (openpyxl may hand back a datetime or a string)."""
        if v is None:
            return ""
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d")
        return str(v).strip()

    # --- read -----------------------------------------------------------------
    def read_jobs(self) -> list[RowJob]:
        jobs: list[RowJob] = []
        for r in range(2, self.ws.max_row + 1):
            row_id = self._get(r, "row_id")
            if not row_id:
                continue  # blank/spacer row
            job = self._build_job(r, str(row_id).strip())
            jobs.append(job)
        return jobs

    def _build_job(self, r: int, row_id: str) -> RowJob:
        status = str(self._get(r, "status") or "").strip()
        visual_type = str(self._get(r, "visual_type") or "").strip()
        fmt = str(self._get(r, "format") or "").strip()
        prompt = str(self._get(r, "prompt") or "").strip()
        platform = str(self._get(r, "platform") or "").strip()
        pillar = str(self._get(r, "pillar") or "").strip()
        hook = str(self._get(r, "hook") or "").strip()
        model_cell = str(self._get(r, "ai_model") or "").strip()
        plan = rules.plan_visual(visual_type, fmt)

        job = RowJob(row_index=r, row_id=row_id, platform=platform, pillar=pillar,
                     hook=hook, status=status, visual_type=visual_type, fmt=fmt,
                     prompt=prompt, model=model_cell, plan=plan,
                     existing_link=self._get(r, "asset_link"),
                     date=self._fmt_date(self._get(r, "date")),
                     day=str(self._get(r, "day") or "").strip(),
                     caption=str(self._get(r, "caption") or "").strip(),
                     hashtags=str(self._get(r, "hashtags") or "").strip(),
                     notes=str(self._get(r, "notes") or "").strip())

        # Only Draft rows are ever generated (per the workflow spec).
        if status.lower() != "draft":
            job.skip_reason = f"status is {status!r}, not Draft"
            return job
        if not plan.generate:
            job.skip_reason = plan.reason
            return job
        if not prompt:
            job.skip_reason = "no prompt in Visual Direction column"
            return job

        job.assets = self._assets_for(job)
        return job

    def _assets_for(self, job: RowJob) -> list[dict]:
        neg = config.DEFAULT_NEGATIVE_PROMPT
        plat_short = rules.platform_short(job.platform, job.row_id)
        pillar_slug = rules.slugify(job.hook or job.pillar)
        model = job.model or (config.DEFAULT_VIDEO_MODEL if job.plan.kind == "video"
                              else config.DEFAULT_IMAGE_MODEL)

        if job.plan.kind == "carousel":
            slides = self._slide_count(job)  # Slides column is authoritative for count
            group = str(self._get(job.row_index, "carousel_group") or "").strip() \
                or f"{job.row_id}_{pillar_slug}"
            job.group = group
            parsed = parse_carousel_prompt(job.prompt)
            assets = []
            for n in range(1, slides + 1):
                seg = parsed[n - 1] if n - 1 < len(parsed) else {}
                desc = (seg.get("desc") or job.prompt) + _NO_TEXT_DIRECTIVE
                assets.append({
                    "id": f"slide-{n}", "type": "image",
                    "prompt": desc,  # per-slide art direction, text-free
                    "negative_prompt": neg + _NO_TEXT_NEGATIVE,
                    "aspect_ratio": job.plan.aspect_ratio,
                    "model": model, "group": group,
                    "usage": f"{plat_short.lower()}-carousel",
                    "overlay_text": seg.get("text"),  # exact words stamped after render
                })
            return assets

        stem = f"{job.row_id}_{plat_short}_{pillar_slug}"
        asset = {"id": stem, "type": job.plan.kind, "prompt": job.prompt,
                 "negative_prompt": neg, "aspect_ratio": job.plan.aspect_ratio,
                 "model": model, "usage": f"{plat_short.lower()}-{job.plan.kind}"}
        if job.plan.kind == "video":
            # Veo 16:9 hero clips: default 720p/6s keeps cost/spec valid (1080p needs 8s).
            asset["resolution"] = "720p"
            asset["duration_seconds"] = 6
        return [asset]

    def _slide_count(self, job: RowJob) -> int:
        raw = self._get(job.row_index, "slides")
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = 0
        return n if n > 0 else 4  # asset-structure doc: carousels are 4–5 slides

    # --- write ----------------------------------------------------------------
    def write_result(self, row_index: int, link: str | None = None,
                     cost: float | str | None = None, model: str | None = None) -> None:
        """Write the Generated Asset Link cell, and/or the cost + AI Model cells.

        A ``link`` that is a URL is written as a real, styled hyperlink; any other
        string (e.g. a ``Failed`` status) is written as plain text with no hyperlink
        or link styling, so a status never masquerades as a clickable link. ``model``
        records the model that actually produced the asset, so a per-run override is
        reflected in the sheet."""
        from openpyxl.styles import Font
        if link is not None and "asset_link" in self.cols:
            cell = self.ws.cell(row_index, self.cols["asset_link"])
            cell.value = link
            if isinstance(link, str) and link.lower().startswith(("http://", "https://")):
                cell.hyperlink = link
                cell.font = Font(color=LINK_FONT_COLOR, underline="single")
            else:
                cell.hyperlink = None
                cell.font = Font()
        if cost is not None and "est_cost" in self.cols:
            self.ws.cell(row_index, self.cols["est_cost"]).value = cost
        if model is not None and "ai_model" in self.cols:
            self.ws.cell(row_index, self.cols["ai_model"]).value = model

    NOTE_MARKER = "[auto]"

    def write_note(self, row_index: int, text: str) -> None:
        """Record an automated note (e.g. a failure reason) in the Notes column,
        preserving any human-written notes. A prior ``[auto]`` line is replaced, so
        re-runs don't stack duplicates; pass an empty text to clear the auto line."""
        if "notes" not in self.cols:
            return
        cell = self.ws.cell(row_index, self.cols["notes"])
        kept = [ln for ln in str(cell.value or "").splitlines()
                if not ln.startswith(self.NOTE_MARKER)]
        if text:
            kept.append(f"{self.NOTE_MARKER} {text}")
        cell.value = "\n".join(kept).strip() or None

    def save(self, path: Path | None = None) -> Path:
        dest = Path(path) if path else self.path
        self.wb.save(dest)
        return dest
