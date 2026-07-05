"""social.sheet_ops — the living Google Sheet and how it syncs with .xlsx files.

The canonical calendar is a native Google Sheet (``Ghedee_Social_Calendar_<id>``, no
version suffix) in 00_Calendar & Docs, edited live by the team and updated in place by
generate. The local .xlsx is Cowork's working format, bridged by:

  upload    a local .xlsx  ->  create the living Google Sheet
  download  the living Google Sheet  ->  a local .xlsx (for Cowork to ingest)
  snapshot  the living Google Sheet  ->  the next _v<N>.xlsx on Drive (frozen record)
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import rules
from ..core import config
from ..core.drive import DriveClient, GSHEET_MIME, XLSX_MIME


def _stderr(msg: str, **_k) -> None:
    print(msg, file=sys.stderr)


def _drive() -> DriveClient:
    return DriveClient(config.credentials_path(), config.token_path(), allow_interactive=False)


def live_sheet_name(calendar_id: str) -> str:
    return f"{rules.CALENDAR_PREFIX}_{calendar_id}"


def _docs_folder(drive: DriveClient, calendar_id: str, quarter_folder: str | None) -> str:
    root = rules.social_calendar_root_id()
    if not root:
        raise RuntimeError("SOCIAL_CALENDAR_ROOT_ID is not set.")
    quarter = quarter_folder or rules.quarter_folder_for(calendar_id)
    if not quarter:
        raise RuntimeError(f"Could not derive a quarter folder from {calendar_id!r}; "
                           "pass quarter_folder.")
    base = drive.find_folder_path(root, [quarter])
    docs = drive.find_folder_path(base, [rules.SUBFOLDER_DOCS]) if base else None
    if not docs:
        raise FileNotFoundError(f"{quarter}/{rules.SUBFOLDER_DOCS} not found on Drive.")
    return docs


def find_live_sheet(drive: DriveClient, docs: str, calendar_id: str) -> dict | None:
    return drive.find_by_name(live_sheet_name(calendar_id), docs, mime=GSHEET_MIME)


def _max_snapshot_version(drive: DriveClient, docs: str, calendar_id: str) -> int:
    best = 0
    for f in drive.list_children(docs):
        parsed = rules.parse_calendar_filename(f["name"])
        if parsed and parsed[0] == calendar_id:
            best = max(best, parsed[1])
    return best


def upload(calendar_id: str, version: int, *, quarter_folder: str | None = None,
           replace: bool = False, emit=None) -> dict:
    """Create the living Google Sheet from the local Ghedee_Social_Calendar_<id>_v<version>.xlsx.
    Refuses if a live sheet already exists (editing happens in place now) unless replace=True,
    which trashes the old one and recreates it from this .xlsx."""
    emit = emit or _stderr
    local = rules.calendar_dir() / rules.calendar_filename(calendar_id, version)
    if not local.exists():
        raise FileNotFoundError(f"local calendar not found: {local}")
    drive = _drive()
    docs = _docs_folder(drive, calendar_id, quarter_folder)
    existing = find_live_sheet(drive, docs, calendar_id)
    if existing and not replace:
        raise RuntimeError(
            f"A live sheet already exists: {existing['name']} ({existing.get('webViewLink')}). "
            "Editing happens in place now — use `download` to pull it local, or pass "
            "replace=True to overwrite it from this .xlsx.")
    if existing and replace:
        drive.trash(existing["id"])
    name = live_sheet_name(calendar_id)
    res = drive.upload_as_google_sheet(local.read_bytes(), name, docs)
    drive.make_shareable(res["id"])
    emit(f"{'replaced' if existing else 'created'} live sheet '{name}' from "
         f"{local.name} -> {res['link']}")
    return {"calendar_id": calendar_id, "from_file": local.name, "live_sheet": name,
            "id": res["id"], "link": res["link"], "replaced": bool(existing)}


def download(calendar_id: str, *, dest_dir=None, quarter_folder: str | None = None,
             emit=None) -> dict:
    """Export the living Google Sheet to a local .xlsx (Ghedee_Social_Calendar_<id>.xlsx)
    so Cowork can ingest the current edits."""
    emit = emit or _stderr
    drive = _drive()
    docs = _docs_folder(drive, calendar_id, quarter_folder)
    live = find_live_sheet(drive, docs, calendar_id)
    if not live:
        raise FileNotFoundError(f"no live sheet '{live_sheet_name(calendar_id)}' on Drive; "
                                "run `upload` first.")
    data = drive.export_as_xlsx(live["id"])
    dest = (Path(dest_dir) if dest_dir else rules.calendar_dir()) / f"{live_sheet_name(calendar_id)}.xlsx"
    dest.write_bytes(data)
    emit(f"downloaded live sheet -> {dest}")
    return {"calendar_id": calendar_id, "path": str(dest), "link": live.get("webViewLink")}


def snapshot(calendar_id: str, *, quarter_folder: str | None = None, emit=None) -> dict:
    """Export the living Google Sheet to the next versioned .xlsx snapshot on Drive."""
    emit = emit or _stderr
    drive = _drive()
    docs = _docs_folder(drive, calendar_id, quarter_folder)
    live = find_live_sheet(drive, docs, calendar_id)
    if not live:
        raise FileNotFoundError(f"no live sheet '{live_sheet_name(calendar_id)}' on Drive; "
                                "run `social upload` first.")
    next_ver = _max_snapshot_version(drive, docs, calendar_id) + 1
    data = drive.export_as_xlsx(live["id"])
    fname = rules.calendar_filename(calendar_id, next_ver)
    up = drive.upload_bytes(data, fname, docs, XLSX_MIME)
    drive.make_shareable(up["id"])
    emit(f"snapshot v{next_ver} -> {fname} ({up['link']})")
    return {"calendar_id": calendar_id, "version": next_ver, "file": fname,
            "id": up["id"], "link": up["link"]}
