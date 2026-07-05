"""core.sheets — minimal Google Sheets client for in-place cell edits.

Used by generate to write the machine-owned columns (Generated Asset Link, Est. Cost,
AI Model, Notes) of the living calendar Sheet directly — no download/re-upload, so the
team's concurrent edits to other cells are never clobbered. A Google Sheet's Drive file
id IS its spreadsheetId.
"""

from __future__ import annotations

from pathlib import Path


class SheetsClient:
    def __init__(self, credentials_path: Path, token_path: Path,
                 allow_interactive: bool = False):
        try:
            from googleapiclient.discovery import build
        except ImportError as e:
            raise RuntimeError(
                "Google API libraries not installed. Run: pip install -r requirements.txt"
            ) from e
        from .google_auth import authorize
        creds = authorize(credentials_path, token_path, allow_interactive)
        self.svc = build("sheets", "v4", credentials=creds)

    def tab_titles(self, spreadsheet_id: str) -> list[str]:
        meta = self.svc.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets(properties(title))").execute()
        return [s["properties"]["title"] for s in meta.get("sheets", [])]

    def pick_tab(self, spreadsheet_id: str, contains: str = "calendar") -> str:
        titles = self.tab_titles(spreadsheet_id)
        return next((t for t in titles if contains in t.lower()),
                    titles[0] if titles else "Sheet1")

    def get_values(self, spreadsheet_id: str, a1_range: str) -> list[list]:
        return self.svc.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=a1_range).execute().get("values", [])

    def batch_update(self, spreadsheet_id: str, updates: list[tuple[str, object]]) -> dict:
        """Write cells. ``updates`` is a list of (A1_range, value); values are entered as
        USER_ENTERED so a plain URL becomes a live link and numbers stay numeric."""
        if not updates:
            return {"updatedCells": 0}
        data = [{"range": a1, "values": [[val]]} for a1, val in updates]
        return self.svc.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
