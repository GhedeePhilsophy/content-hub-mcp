"""social — the Social Calendar workflow (Content Hub workflow #1).

  rules      calendar naming, id→folder, aspect-ratio rules, Drive layout
  calendar   parse the calendar → jobs; write results back
  workflow   generate_media
  sheet_ops  upload / download / snapshot for the living Google Sheet
  preview    the HTML review page

Operations are re-exported here so callers can use
`from content_hub.social import generate_media` etc.
"""

from .workflow import generate_media
from .sheet_ops import create, upload, download, snapshot

__all__ = ["generate_media", "create", "upload", "download", "snapshot",
           "rules", "calendar", "workflow", "sheet_ops", "preview"]
