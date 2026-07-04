"""social — the Social Calendar workflow (Content Hub workflow #1).

  rules      calendar naming, quarter→folder, aspect-ratio rules, Drive layout
  calendar   parse the .xlsx source of truth → jobs; write results back
  workflow   the 3 operations (generate_media / upload_calendar / download_latest)

The three operations are re-exported here so callers can use
`from content_hub.social import generate_media` etc. Blog and email will be
sibling packages with the same shape.
"""

from .workflow import generate_media, upload_calendar, download_latest

__all__ = ["generate_media", "upload_calendar", "download_latest",
           "rules", "calendar", "workflow"]
