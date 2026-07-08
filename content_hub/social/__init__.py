"""social — the Social Calendar workflow (Content Hub workflow #1).

  rules      calendar naming, id→folder, aspect-ratio rules, Drive layout
  calendar   parse the calendar → jobs; write results back
  workflow   generate_media
  sheet_ops  create the living Google Sheet
  edit_ops   direct in-place cell edits + bulk row appends to the living sheet
  preview    the HTML review page

Operations are re-exported here so callers can use
`from content_hub.social import generate_media` etc.
"""

from .workflow import generate_media
from .sheet_ops import create
from .edit_ops import edit_rows, add_rows

__all__ = ["generate_media", "create",
           "edit_rows", "add_rows",
           "rules", "calendar", "workflow", "sheet_ops", "edit_ops", "preview"]
