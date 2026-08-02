"""social — the Social Calendar workflow (Content Hub workflow #1).

  rules      calendar naming, id→folder, aspect-ratio rules, Drive layout
  calendar   parse the calendar → jobs; write results back
  workflow   generate_media
  sheet_ops  create the living Google Sheet
  edit_ops   direct in-place cell edits + bulk row appends to the living sheet;
             describe the sheet's columns/rows (look before you write) and
             get_rows to read rows back in full
  preview    the HTML review page
  exporters  bulk-import files for external schedulers (metricool, …)

Operations are re-exported here so callers can use
`from content_hub.social import generate_media` etc.
"""

from .workflow import generate_media
from .sheet_ops import create
from .edit_ops import edit_rows, add_rows, describe, get_rows
from .exporters import export as export_calendar
from .audit import audit_calendar

__all__ = ["generate_media", "create",
           "edit_rows", "add_rows", "describe", "get_rows",
           "export_calendar", "audit_calendar",
           "rules", "specs", "calendar", "workflow", "sheet_ops", "edit_ops", "preview",
           "exporters", "audit"]
