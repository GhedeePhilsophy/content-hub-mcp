"""social — the Social Calendar workflow orchestrator.

Ties the calendar sheet, the generation engine, and Drive together into the three
operations the MCP server exposes:

  upload_calendar   push a local draft .xlsx to  <quarter>/00_Calendar & Docs/
  generate_media    read Draft rows -> generate the missing ones -> upload
                    route-by-type -> write the Drive link + cost back into the sheet
  download_latest   pull the newest Ghedee_Social_Calendar_<id>_v<n>.xlsx to local

Every operation takes ``mode`` = dry-run | mock | live:
  dry-run  plan only. No Drive, no API, nothing written.
  mock     real pipeline with placeholder files. Uploads and write-back are
           routed to a SAFE mock destination (a mock root, or a '_mock rehearsal'
           subfolder) and a *.mock.xlsx copy — production assets are never touched.
  live     the real thing.

Only Status=Draft rows are ever generated, and a row is skipped if its asset is
already on Drive (deleting the Drive file is how you request a regeneration).
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import rules
from .calendar import Calendar
from ..core import config, media
from ..core.drive import FOLDER_MIME

MOCK_SUBFOLDER = "_mock rehearsal"
FAILED_TEXT = "Failed"  # written into the asset-link cell when a row can't be produced


def _stderr_emit(msg: str, *, err: bool = False) -> None:
    print(msg, file=sys.stderr)


def _drive_client(interactive: bool = False):
    from ..core.drive import DriveClient
    return DriveClient(config.credentials_path(), config.token_path(),
                       allow_interactive=interactive)


def _local_calendar_path(calendar_id: str, version: int) -> Path:
    return rules.calendar_dir() / rules.calendar_filename(calendar_id, version)


def _resolve_base_folder(drive, calendar_id: str, mode: str,
                         quarter_folder: str | None) -> tuple[str, str]:
    """Return (base_folder_id, quarter_name). For live this is the quarter folder
    under the Social Calendar root; for mock it's a sandbox that mirrors it."""
    root = rules.social_calendar_root_id()
    if not root:
        raise RuntimeError("SOCIAL_CALENDAR_ROOT_ID is not set — point it at the "
                           "'Social Calendar' Drive folder id.")
    quarter = quarter_folder or rules.quarter_folder_for(calendar_id)
    if not quarter:
        raise RuntimeError(f"Could not derive a quarter folder from calendar_id "
                           f"{calendar_id!r}; pass quarter_folder explicitly.")
    if mode == "mock":
        mock_root = rules.social_calendar_mock_root_id()
        if mock_root:
            return drive.ensure_path(mock_root, [quarter]), quarter
        quarter_id = drive.find_or_create_folder(quarter, root)
        return drive.ensure_path(quarter_id, [MOCK_SUBFOLDER]), quarter
    return drive.find_or_create_folder(quarter, root), quarter


def _kind_parent(drive, base_folder: str, job) -> str:
    """The route-by-type folder to check for an existing asset, and (for image/video)
    to upload into. Carousels check/inhabit the 03_Carousels parent; the per-set group
    subfolder is created only when actually generating — not during the existence check."""
    if job.plan.kind == "carousel":
        return drive.ensure_path(base_folder, list(rules.SUBFOLDER_CAROUSELS))
    if job.plan.kind == "video":
        return drive.ensure_path(base_folder, list(rules.SUBFOLDER_VIDEO))
    return drive.ensure_path(base_folder, list(rules.SUBFOLDER_IMAGES))


def _already_on_drive(drive, parent: str, job) -> str | None:
    """Existing Drive link if this row's asset is already there, else None. Keyed on
    the stable Row ID prefix (``{row_id}_``), NOT the hook-derived slug — so editing a
    caption never orphans an already-generated file. A carousel counts as present when
    a group subfolder named ``{row_id}_...`` exists and holds at least one file."""
    if job.plan.kind == "carousel":
        for child in drive.list_children(parent):
            if (child.get("mimeType") == FOLDER_MIME
                    and child["name"].startswith(f"{job.row_id}_")):
                has_file = any(f.get("mimeType") != FOLDER_MIME
                               for f in drive.list_children(child["id"]))
                if has_file:
                    return drive.get_link(child["id"])
        return None
    hits = drive.find_by_prefix(f"{job.row_id}_", parent)
    return hits[0].get("webViewLink") if hits else None


# --- operations ------------------------------------------------------------
def generate_media(calendar_id: str, version: int, mode: str = "dry-run", *,
                   only: str | None = None, quarter_folder: str | None = None,
                   image_model: str | None = None, video_model: str | None = None,
                   video_duration: int | None = None, emit=None) -> dict:
    """Generate the missing AI visuals for a calendar's Draft rows and (mock/live)
    upload them + write links back. See module docstring for mode semantics.

    ``image_model`` / ``video_model`` override the per-row AI Model for this run
    only (the sheet is unchanged) — e.g. point video rows at a lighter Veo model
    to dodge a quota without editing the calendar. ``video_duration`` sets the
    target clip length in seconds (Veo builds >8s clips by chaining extensions)."""
    emit = emit or _stderr_emit
    if mode not in ("dry-run", "mock", "live"):
        raise ValueError(f"mode must be dry-run|mock|live, got {mode!r}")

    path = _local_calendar_path(calendar_id, version)
    if not path.exists():
        raise FileNotFoundError(f"local calendar not found: {path}")
    cal = Calendar(path)
    jobs = cal.read_jobs()

    def _wanted(job) -> bool:
        if not job.in_scope:
            return False
        if only == "image":
            return job.plan.kind in ("image", "carousel")
        if only == "video":
            return job.plan.kind == "video"
        return True

    in_scope = [j for j in jobs if _wanted(j)]
    # Overrides: explicit arg (CLI flag / tool param) wins over the env default
    # (VIDEO_MODEL / IMAGE_MODEL / VIDEO_DURATION), which beats the sheet's AI Model.
    video_model = video_model or config.video_model_override()
    image_model = image_model or config.image_model_override()
    video_duration = video_duration if video_duration is not None else config.video_duration_override()
    # Apply before planning, so dry-run cost reflects them.
    for job in in_scope:
        for a in job.assets:
            if a["type"] == "video":
                if video_model:
                    a["model"] = video_model
                if video_duration:
                    a["duration_seconds"] = video_duration
            elif a["type"] == "image" and image_model:
                a["model"] = image_model
    out_dir = config.generated_dir() / calendar_id
    result = {"calendar_id": calendar_id, "version": version, "mode": mode,
              "rows_total": len(jobs), "in_scope": len(in_scope),
              "generated": 0, "skipped_existing": 0, "failed": 0,
              "estimated_cost_usd": 0.0, "rows": [], "hints": [], "writeback_file": None}

    # --- dry-run: plan only, no Drive, no API --------------------------------
    if mode == "dry-run":
        total = 0.0
        for j in in_scope:
            rec = media.run_batch(j.assets, defaults=media.DEFAULTS,
                                       out_dir=out_dir, mode="dry-run", emit=emit,
                                       batch_id=j.row_id)
            cost = rec["estimated_cost_usd"]
            total += cost
            result["rows"].append({"row_id": j.row_id, "kind": j.plan.kind,
                                   "aspect_ratio": j.plan.aspect_ratio,
                                   "assets": len(j.assets), "action": "would-generate",
                                   "cost_usd": round(cost, 4)})
        result["estimated_cost_usd"] = round(total, 2)
        result["note"] = ("dry-run cannot check Drive; every in-scope Draft row is "
                          "listed as would-generate. Costs are the worst case "
                          "(nothing skipped as already-present).")
        return result

    # --- mock / live: Drive-backed -------------------------------------------
    drive = _drive_client(interactive=False)
    base_folder, quarter = _resolve_base_folder(drive, calendar_id, mode, quarter_folder)
    result["quarter_folder"] = quarter
    client = types = None
    if mode == "live":
        client, types = media.init_live_client()

    hints: set[str] = set()
    for job in in_scope:
        parent = _kind_parent(drive, base_folder, job)
        existing = _already_on_drive(drive, parent, job)
        if existing:
            result["skipped_existing"] += 1
            est = media.estimate_cost(job.assets)
            result["rows"].append({"row_id": job.row_id, "kind": job.plan.kind,
                                   "action": "skipped-existing", "link": existing,
                                   "cost_usd": est})
            # sync the cell to the live Drive link + estimated cost for a skipped row
            cal.write_result(job.row_index, link=existing, cost=est)
            cal.write_note(job.row_index, "")  # clear any stale failure note
            continue

        rec = media.run_batch(job.assets, defaults=media.DEFAULTS,
                                   out_dir=out_dir, mode=mode, emit=emit,
                                   batch_id=job.row_id, client=client, types=types)
        hints.update(rec.get("hints", []))
        outputs = [o for o in rec["outputs"] if not o.get("dry_run")]
        if rec["errors"] or not outputs:
            result["failed"] += 1
            err = rec["errors"][0]["reason"] if rec["errors"] else "no output produced"
            cal.write_result(job.row_index, link=FAILED_TEXT)
            cal.write_note(job.row_index, f"generate failed: {err}")
            result["rows"].append({"row_id": job.row_id, "kind": job.plan.kind,
                                   "action": "failed", "error": err})
            continue

        # Create the carousel's group subfolder only now, at upload time.
        dest = (drive.ensure_path(parent, [job.group])
                if job.plan.kind == "carousel" else parent)
        try:
            link = _upload_row(drive, dest, job, outputs)
        except Exception as e:
            short, hint = media.friendly_error(e)
            if hint:
                hints.add(hint)
            result["failed"] += 1
            cal.write_result(job.row_index, link=FAILED_TEXT)
            cal.write_note(job.row_index, f"upload failed: {short}")
            result["rows"].append({"row_id": job.row_id, "kind": job.plan.kind,
                                   "action": "upload-failed", "error": short})
            continue

        cost = round(sum(o.get("est_cost_usd", 0) for o in outputs), 4)
        used_model = outputs[0].get("model")  # actual model (reflects any override)
        cal.write_result(job.row_index, link=link, cost=cost, model=used_model)
        cal.write_note(job.row_index, "")  # clear any stale failure note
        result["generated"] += 1
        result["estimated_cost_usd"] = round(result["estimated_cost_usd"] + cost, 4)
        result["rows"].append({"row_id": job.row_id, "kind": job.plan.kind,
                               "action": "generated", "link": link, "cost_usd": cost})

    # write the updated sheet: the working file for live, a *.mock.xlsx copy for mock
    if mode == "mock":
        dest_path = path.with_name(path.stem + ".mock.xlsx")
    else:
        dest_path = path
    cal.save(dest_path)
    result["writeback_file"] = str(dest_path)
    result["hints"] = sorted(hints)
    emit(f"\nDone [{mode}]. generated={result['generated']} "
         f"skipped-existing={result['skipped_existing']} failed={result['failed']} "
         f"est.cost~${result['estimated_cost_usd']}. Sheet -> {dest_path.name}")
    return result


def _upload_row(drive, dest_folder: str, job, outputs: list[dict]) -> str | None:
    """Upload a row's generated files and return the link to write into the sheet:
    the group-folder link for a carousel, else the file link."""
    file_link = None
    for o in outputs:
        up = drive.upload(Path(o["file"]), dest_folder)
        o["drive_id"], o["drive_link"] = up["id"], up["link"]
        drive.make_shareable(up["id"])
        file_link = file_link or up["link"]
    if job.plan.kind == "carousel":
        # one folder link per carousel row (Cowork prompt step 5)
        return drive.make_shareable(dest_folder)
    return file_link


def upload_calendar(calendar_id: str, version: int, mode: str = "dry-run", *,
                    quarter_folder: str | None = None, emit=None) -> dict:
    """Upload the local draft .xlsx into <quarter>/00_Calendar & Docs/."""
    emit = emit or _stderr_emit
    if mode not in ("dry-run", "mock", "live"):
        raise ValueError(f"mode must be dry-run|mock|live, got {mode!r}")
    path = _local_calendar_path(calendar_id, version)
    if not path.exists():
        raise FileNotFoundError(f"local calendar not found: {path}")

    result = {"calendar_id": calendar_id, "version": version, "mode": mode,
              "file": path.name, "action": None, "link": None}
    if mode == "dry-run":
        quarter = quarter_folder or rules.quarter_folder_for(calendar_id) or "<unresolved>"
        result["action"] = "would-upload"
        result["destination"] = f"{quarter}/{rules.SUBFOLDER_DOCS}/{path.name}"
        emit(f"[dry-run] would upload {path.name} -> {result['destination']}")
        return result

    drive = _drive_client(interactive=False)
    base_folder, quarter = _resolve_base_folder(drive, calendar_id, mode, quarter_folder)
    docs = drive.ensure_path(base_folder, [rules.SUBFOLDER_DOCS])
    up = drive.upload(path, docs)
    link = drive.make_shareable(up["id"])
    result.update(action="uploaded", link=link, quarter_folder=quarter,
                  destination=f"{quarter}/{rules.SUBFOLDER_DOCS}/{path.name}")
    emit(f"[{mode}] uploaded {path.name} -> {result['destination']}")
    return result


def download_latest(calendar_id: str, *, quarter_folder: str | None = None,
                    dest_dir: Path | None = None, emit=None) -> dict:
    """Download the highest-version draft for this calendar from Drive to local."""
    emit = emit or _stderr_emit
    drive = _drive_client(interactive=False)
    root = rules.social_calendar_root_id()
    if not root:
        raise RuntimeError("SOCIAL_CALENDAR_ROOT_ID is not set.")
    quarter = quarter_folder or rules.quarter_folder_for(calendar_id)
    if not quarter:
        raise RuntimeError(f"Could not derive a quarter folder from {calendar_id!r}; "
                           "pass quarter_folder.")
    docs = drive.find_folder_path(root, [quarter, rules.SUBFOLDER_DOCS])
    if not docs:
        raise FileNotFoundError(f"{quarter}/{rules.SUBFOLDER_DOCS} not found on Drive.")

    best = None  # (version, file dict)
    for f in drive.list_children(docs):
        parsed = rules.parse_calendar_filename(f["name"])
        if parsed and parsed[0] == calendar_id:
            if best is None or parsed[1] > best[0]:
                best = (parsed[1], f)
    if not best:
        raise FileNotFoundError(f"no {rules.CALENDAR_PREFIX}_{calendar_id}_v*.xlsx "
                                f"in {quarter}/{rules.SUBFOLDER_DOCS}.")
    version, f = best
    dest = (Path(dest_dir) if dest_dir else rules.calendar_dir()) / f["name"]
    drive.download_file(f["id"], dest)
    emit(f"downloaded {f['name']} (v{version}) -> {dest}")
    return {"calendar_id": calendar_id, "version": version, "file": f["name"],
            "path": str(dest)}
