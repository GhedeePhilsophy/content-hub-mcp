"""social.sheet_ops — create the living Google Sheet.

The canonical calendar is a native Google Sheet (``Ghedee_Social_Calendar_<id>``, no
version suffix) in 00_Calendar & Docs, edited live by the team, populated by
``social.edit_ops`` (add_rows / edit_rows), and updated in place by generate. There is
no local-.xlsx round-trip — everything goes to the live sheet.

  create    the Drive folder tree + an empty, styled living Google Sheet shell
"""

from __future__ import annotations

import sys

from . import rules
from ..core import config
from ..core.drive import DriveClient, GSHEET_MIME


def _stderr(msg: str, **_k) -> None:
    print(msg, file=sys.stderr)


def _drive() -> DriveClient:
    return DriveClient(config.credentials_path(), config.token_path(), allow_interactive=False)


def live_sheet_name(calendar_id: str) -> str:
    return f"{rules.CALENDAR_PREFIX}_{calendar_id}"


def _docs_folder(drive: DriveClient, calendar_id: str) -> str:
    root = rules.social_calendar_root_id()
    if not root:
        raise RuntimeError("SOCIAL_CALENDAR_ROOT_ID is not set.")
    folder = rules.calendar_folder(calendar_id)
    base = drive.find_folder_path(root, [folder])
    docs = drive.find_folder_path(base, [rules.SUBFOLDER_DOCS]) if base else None
    if not docs:
        raise FileNotFoundError(f"{folder}/{rules.SUBFOLDER_DOCS} not found on Drive.")
    return docs


def find_live_sheet(drive: DriveClient, docs: str, calendar_id: str) -> dict | None:
    return drive.find_by_name(live_sheet_name(calendar_id), docs, mime=GSHEET_MIME)


def create(calendar_id: str, *, replace: bool = False,
           tab_title: str | None = None, emit=None) -> dict:
    """Initialise a brand-new Social Calendar: the Drive folder tree + the living sheet.

    Creates the calendar folder under the Social Calendar root (named by the Calendar ID
    verbatim) and its subfolders (00_Calendar & Docs, 02_AI Visuals/Images + /Video,
    03_Carousels), then creates the living Google Sheet as an empty, styled header-only
    shell in 00_Calendar & Docs. Cowork fills it in directly with ``social_add_rows`` —
    there is no local .xlsx to hand back.

    ``calendar_id`` may be a quarter (Q3_2026), a date range, or a single day — it is just
    the folder name. Refuses if a living sheet already exists unless replace=True (which
    trashes the old one first)."""
    from .calendar import DEFAULT_TAB_TITLE, new_shell_bytes
    emit = emit or _stderr
    root = rules.social_calendar_root_id()
    if not root:
        raise RuntimeError("SOCIAL_CALENDAR_ROOT_ID is not set — point it at the "
                           "'Social Calendar' Drive folder id.")
    drive = _drive()
    folder = rules.calendar_folder(calendar_id)
    base = drive.find_or_create_folder(folder, root)
    docs = drive.ensure_path(base, [rules.SUBFOLDER_DOCS])
    # Pre-create the asset folders generate later routes uploads into.
    drive.ensure_path(base, list(rules.SUBFOLDER_WIAH_VIDEOS))
    drive.ensure_path(base, list(rules.SUBFOLDER_IMAGES))
    drive.ensure_path(base, list(rules.SUBFOLDER_VIDEO))
    drive.ensure_path(base, list(rules.SUBFOLDER_CAROUSELS))

    existing = find_live_sheet(drive, docs, calendar_id)
    if existing and not replace:
        raise RuntimeError(
            f"A live sheet already exists: {existing['name']} ({existing.get('webViewLink')}). "
            "Add rows with `social_add_rows`, or pass replace=True to recreate an empty shell.")
    if existing and replace:
        drive.trash(existing["id"])

    shell = new_shell_bytes(tab_title or DEFAULT_TAB_TITLE)
    name = live_sheet_name(calendar_id)
    sheet = drive.upload_as_google_sheet(shell, name, docs)
    drive.make_shareable(sheet["id"])

    emit(f"created calendar '{folder}': live sheet '{name}' -> {sheet['link']}")
    return {"calendar_id": calendar_id, "folder": folder, "live_sheet": name,
            "id": sheet["id"], "link": sheet["link"], "replaced": bool(existing)}
