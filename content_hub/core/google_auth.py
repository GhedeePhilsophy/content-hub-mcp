"""core.google_auth — one OAuth flow shared by the Drive and Sheets clients.

Uses two scopes:
  - drive        : full Drive so the app can see EXISTING folders it didn't create
                   (the narrower drive.file scope can't, which would duplicate folders).
  - spreadsheets : in-place cell edits on the living calendar Google Sheet.

One-time consent (Desktop-app client secret at credentials.json) opens a browser
and caches the token at token.json for headless re-runs. When the scope list grows,
the cached token no longer covers it — has_scopes() detects that and forces a fresh
consent, so upgrading is `python -m content_hub.cli auth` and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _granted_scopes(token_path: Path) -> set[str]:
    """The scopes Google actually granted, from the token file (from_authorized_user_file
    overrides creds.scopes with the requested list, so we read the file directly)."""
    try:
        return set(json.loads(token_path.read_text(encoding="utf-8")).get("scopes", []))
    except Exception:
        return set()


def authorize(credentials_path: Path, token_path: Path, allow_interactive: bool = True):
    """Return valid Google OAuth credentials covering SCOPES, refreshing or prompting
    for consent as needed. Raises RuntimeError with an actionable message when it
    can't (missing client secret, or headless with no/insufficient token)."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:
        raise RuntimeError(
            "Google auth libraries not installed. Run: pip install -r requirements.txt"
        ) from e

    creds = None
    has_all = set(SCOPES).issubset(_granted_scopes(token_path))
    if token_path.exists() and has_all:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif allow_interactive:
            if not credentials_path.exists():
                raise RuntimeError(
                    f"OAuth client secret not found at {credentials_path}. Create a "
                    "Desktop-app OAuth client in Google Cloud Console and save it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        else:
            raise RuntimeError(
                f"No valid Google token at {token_path} (or it lacks the Sheets scope). "
                "Run `python -m content_hub.cli auth` once to grant Drive + Sheets."
            )
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds
