"""drive — Google Drive client (OAuth "as you").

Refactored from drive_upload.py. Adds what the Social Calendar workflow needs on
top of the original upload/find-or-create-folder behaviour:

  - file_exists / find_by_prefix  — the idempotency check (skip generation when
    the asset is already on Drive; a deleted file is the regenerate signal).
  - folder links + make_shareable — so a carousel row gets one clickable link.
  - download_file / latest_version — pull the newest calendar draft back down.

Auth (one-time): an OAuth *Desktop app* client secret at credentials.json.
First run opens a browser to consent; the token is cached at token.json for
headless re-runs. Under the MCP server this consent must be done out-of-band —
the server itself runs off the cached token (see require_token()).

Scope: full 'drive' — needed so the app can see EXISTING folders it did not
create (the narrower drive.file scope can't, which would duplicate folders).
"""

from __future__ import annotations

import re
from pathlib import Path

FOLDER_MIME = "application/vnd.google-apps.folder"
GSHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".mp4": "video/mp4", ".mov": "video/quicktime", ".xlsx": XLSX_MIME}

# A Drive id embedded in a share link: a file (/file/d/<id>/…), a folder
# (/drive/folders/<id>), or the legacy ?id=<id> form.
_DRIVE_ID_RE = re.compile(r"/(?:d|folders)/([A-Za-z0-9_-]+)|[?&]id=([A-Za-z0-9_-]+)")


def file_id_from_link(link: str | None) -> str | None:
    """Extract a Drive id from a share link — a file (``/file/d/<id>/…``), a folder
    (``/drive/folders/<id>``), or the legacy ``?id=<id>`` form. Returns None for a
    non-Drive URL or unparseable value. (A folder link resolves to the folder's id; the
    caller decides how to handle a folder vs a file.)"""
    if not link:
        return None
    m = _DRIVE_ID_RE.search(link)
    return (m.group(1) or m.group(2)) if m else None


def _q_escape(name: str) -> str:
    """Escape a value for a Drive query string literal."""
    return name.replace("\\", "\\\\").replace("'", "\\'")


class DriveClient:
    def __init__(self, credentials_path: Path, token_path: Path,
                 allow_interactive: bool = True):
        try:
            from googleapiclient.discovery import build
        except ImportError as e:
            raise RuntimeError(
                "Drive libraries not installed. Run: pip install -r requirements.txt"
            ) from e
        from .google_auth import authorize
        self.creds = authorize(credentials_path, token_path, allow_interactive)
        self.svc = build("drive", "v3", credentials=self.creds)
        self._folder_cache: dict[tuple[str, str], str] = {}

    # --- folders --------------------------------------------------------------
    def _find_folder(self, name: str, parent_id: str) -> str | None:
        q = (f"mimeType='{FOLDER_MIME}' and trashed=false "
             f"and name='{_q_escape(name)}' and '{parent_id}' in parents")
        res = self.svc.files().list(
            q=q, fields="files(id,name)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def find_or_create_folder(self, name: str, parent_id: str) -> str:
        key = (parent_id, name)
        if key in self._folder_cache:
            return self._folder_cache[key]
        fid = self._find_folder(name, parent_id)
        if not fid:
            meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
            fid = self.svc.files().create(
                body=meta, fields="id", supportsAllDrives=True).execute()["id"]
        self._folder_cache[key] = fid
        return fid

    def ensure_path(self, parent_id: str, names: list[str]) -> str:
        """Find-or-create a chain of subfolders; return the deepest folder id."""
        cur = parent_id
        for name in names:
            cur = self.find_or_create_folder(name, cur)
        return cur

    def find_folder_path(self, parent_id: str, names: list[str]) -> str | None:
        """Like ensure_path but read-only: return the deepest id, or None if any
        segment is missing. Used to look before generating (don't create on read)."""
        cur = parent_id
        for name in names:
            cur = self._find_folder(name, cur)
            if cur is None:
                return None
        return cur

    # --- files ----------------------------------------------------------------
    def _list(self, q: str, fields: str = "files(id,name,webViewLink)") -> list[dict]:
        out, token = [], None
        while True:
            res = self.svc.files().list(
                q=q, fields=f"nextPageToken,{fields}", pageSize=100, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            out.extend(res.get("files", []))
            token = res.get("nextPageToken")
            if not token:
                return out

    def find_file(self, name: str, parent_id: str) -> str | None:
        q = (f"mimeType!='{FOLDER_MIME}' and trashed=false "
             f"and name='{_q_escape(name)}' and '{parent_id}' in parents")
        files = self._list(q, fields="files(id,name)")
        return files[0]["id"] if files else None

    def find_by_prefix(self, prefix: str, parent_id: str) -> list[dict]:
        """Files in ``parent_id`` whose name starts with ``prefix`` (case-sensitive
        on Drive's side is not guaranteed, so we also filter locally). The Row ID
        prefix (e.g. '27JUL-IG-01_') is the stable idempotency key."""
        q = (f"mimeType!='{FOLDER_MIME}' and trashed=false "
             f"and name contains '{_q_escape(prefix)}' and '{parent_id}' in parents")
        return [f for f in self._list(q) if f["name"].startswith(prefix)]

    def upload(self, local_path: Path, parent_id: str) -> dict:
        """Upload (or overwrite same-named) file into parent_id. Returns {id, link, name}."""
        from googleapiclient.http import MediaFileUpload
        mime = MIME_BY_EXT.get(local_path.suffix.lower(), "application/octet-stream")
        media = MediaFileUpload(str(local_path), mimetype=mime, resumable=False)
        existing = self.find_file(local_path.name, parent_id)
        if existing:
            f = self.svc.files().update(
                fileId=existing, media_body=media, fields="id,webViewLink,name",
                supportsAllDrives=True).execute()
        else:
            meta = {"name": local_path.name, "parents": [parent_id]}
            f = self.svc.files().create(
                body=meta, media_body=media, fields="id,webViewLink,name",
                supportsAllDrives=True).execute()
        return {"id": f["id"], "link": f.get("webViewLink"), "name": f.get("name")}

    def get_link(self, file_id: str) -> str | None:
        f = self.svc.files().get(fileId=file_id, fields="webViewLink",
                                 supportsAllDrives=True).execute()
        return f.get("webViewLink")

    def get_file(self, file_id: str,
                 fields: str = "id,name,mimeType,md5Checksum,modifiedTime,webViewLink") -> dict:
        """A file's metadata by id. Used to resolve a Generated Asset Link into the
        bytes + md5 the preview needs (md5/modifiedTime is the thumbnail cache key)."""
        return self.svc.files().get(fileId=file_id, fields=fields,
                                    supportsAllDrives=True).execute()

    def make_shareable(self, file_id: str) -> str | None:
        """Grant 'anyone with the link — Viewer' and return the webViewLink. Used so
        the calendar's Drive link is openable by reviewers / the scheduler."""
        try:
            self.svc.permissions().create(
                fileId=file_id, body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True).execute()
        except Exception:
            pass  # already shared, or insufficient rights on a managed drive
        return self.get_link(file_id)

    def download_file(self, file_id: str, dest_path: Path) -> Path:
        from googleapiclient.http import MediaIoBaseDownload
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        req = self.svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        with open(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return dest_path

    def download_bytes(self, file_id: str) -> bytes:
        """Download a file's content into memory (used to inline assets in a preview)."""
        import io
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(
            buf, self.svc.files().get_media(fileId=file_id, supportsAllDrives=True))
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    def list_children(self, parent_id: str) -> list[dict]:
        q = f"trashed=false and '{parent_id}' in parents"
        return self._list(
            q, fields="files(id,name,mimeType,md5Checksum,modifiedTime,size,webViewLink)")

    # --- Google Sheet conversion / snapshots ----------------------------------
    def find_by_name(self, name: str, parent_id: str, mime: str | None = None) -> dict | None:
        q = (f"trashed=false and name='{_q_escape(name)}' and '{parent_id}' in parents"
             + (f" and mimeType='{mime}'" if mime else ""))
        files = self._list(q, fields="files(id,name,mimeType,webViewLink)")
        return files[0] if files else None

    def upload_as_google_sheet(self, data: bytes, name: str, parent_id: str) -> dict:
        """Create a NEW native Google Sheet from .xlsx bytes (Drive converts on import)."""
        from googleapiclient.http import MediaInMemoryUpload
        media = MediaInMemoryUpload(data, mimetype=XLSX_MIME, resumable=False)
        body = {"name": name, "mimeType": GSHEET_MIME, "parents": [parent_id]}
        f = self.svc.files().create(
            body=body, media_body=media, fields="id,name,webViewLink",
            supportsAllDrives=True).execute()
        return {"id": f["id"], "name": f["name"], "link": f.get("webViewLink")}

    def trash(self, file_id: str) -> None:
        self.svc.files().update(fileId=file_id, body={"trashed": True},
                                supportsAllDrives=True).execute()

    def export_as_xlsx(self, file_id: str) -> bytes:
        """Export a native Google Sheet to .xlsx bytes (for a versioned snapshot)."""
        import io
        from googleapiclient.http import MediaIoBaseDownload
        req = self.svc.files().export_media(fileId=file_id, mimeType=XLSX_MIME)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    def upload_bytes(self, data: bytes, name: str, parent_id: str, mime: str) -> dict:
        """Create a new file from in-memory bytes (e.g. a snapshot .xlsx)."""
        from googleapiclient.http import MediaInMemoryUpload
        media = MediaInMemoryUpload(data, mimetype=mime, resumable=False)
        f = self.svc.files().create(
            body={"name": name, "parents": [parent_id]}, media_body=media,
            fields="id,name,webViewLink", supportsAllDrives=True).execute()
        return {"id": f["id"], "name": f["name"], "link": f.get("webViewLink")}
