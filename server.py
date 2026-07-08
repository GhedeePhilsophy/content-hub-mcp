#!/usr/bin/env python3
"""server.py — Content Hub MCP server.

Backs the Ghedee Content Hub Cowork workflow. Phase 1 exposes the Social Calendar
tools only (Blog Posts and Emails are planned). Runs over stdio: all human-readable
progress is written to stderr, and stdout is reserved for the MCP protocol.

Run locally (after `pip install -r requirements.txt`):
    python server.py

Every tool takes ``mode`` = 'dry-run' | 'mock' | 'live':
    dry-run  plan only — no Drive, no API, nothing written.
    mock     placeholder files; uploads + write-back routed to a SAFE mock
             destination and a *.mock.xlsx copy. Production is never touched.
    live     the real run (spends credits, writes to Drive + the working sheet).
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from content_hub.core import config
from content_hub import social

config.load_dotenv()

mcp = FastMCP("ghedee-content-hub")


def _emit(msg: str, *, err: bool = False) -> None:
    # stdout is the MCP channel; progress must go to stderr.
    print(msg, file=sys.stderr)


@mcp.tool()
def social_generate_media(calendar_id: str, mode: str = "dry-run",
                          only: str | None = None,
                          image_model: str | None = None,
                          video_model: str | None = None,
                          video_duration: int | None = None) -> dict:
    """Generate the missing AI visuals for the LIVING Google Sheet's Draft rows, upload
    them to Google Drive, and write each Drive link / cost / model / notes back INTO THE
    LIVING SHEET in place (live mode) — no download/re-upload, so concurrent human edits
    aren't clobbered.

    Only rows with Status=Draft and Visual Type 'AI text-to-image' / 'AI text-to-video'
    are generated; a row is SKIPPED if its asset is already on Drive (delete the Drive
    file to force a regeneration). 'Recorded video of Wiah' rows are never generated.
    A failed row is marked 'Failed' in its asset-link cell with the reason in Notes.

    Args:
        calendar_id: e.g. 'Q3_2026'.
        mode: 'dry-run' (plan + cost only, sheet untouched), 'mock' (safe rehearsal to a
            mock Drive dest + a local *.mock.xlsx), or 'live' (writes the live sheet).
        only: optionally limit to 'image' or 'video'.
        video_model / image_model: override the model for this run only (e.g. video
            'veo-3.1-fast-generate-preview', image 'gpt-image-2'); the model actually used
            is written to the sheet.
        video_duration: target video length in seconds; Veo chains extensions past 8s (to 30).

    Returns a per-row summary, links, costs, and (live) the count of cells updated.
    """
    return social.generate_media(calendar_id, mode=mode, only=only,
                                 image_model=image_model, video_model=video_model,
                                 video_duration=video_duration, emit=_emit)


@mcp.tool()
def social_create_calendar(calendar_id: str, replace: bool = False) -> dict:
    """Initialise a brand-new Social Calendar (call this to START a new calendar). Creates
    the Google Drive folder tree (the calendar folder + 00_Calendar & Docs, 02_AI Visuals/
    Images + /Video, 03_Carousels) and creates the LIVING Google Sheet as an empty, styled
    header-only shell in 00_Calendar & Docs. Cowork then fills it in directly with
    social_add_rows — there is no local file to hand back.

    calendar_id may be a quarter (e.g. 'Q3_2026'), a date range, or a single day — it is NOT
    required to be a quarter. Whatever it is, it becomes the Drive folder name verbatim.

    Args:
        calendar_id: the new calendar's id (a quarter, a date range, or a single day); this
            is used verbatim as the Drive folder name.
        replace: recreate an empty shell even if a live sheet already exists (trashes the old).

    Returns the folder name and the living sheet name/id/link.
    """
    from content_hub.social import sheet_ops
    return sheet_ops.create(calendar_id, replace=replace, emit=_emit)


@mcp.tool()
def social_build_preview(calendar_id: str, version: int | None = None,
                         no_cache: bool = False, publish: bool = True) -> dict:
    """Build a self-contained HTML review page of a Social Calendar's posts, each
    rendered as a mockup in its platform's chrome (Instagram / Facebook / TikTok),
    grouped by week, plus an Instagram profile-grid view. Reads the LIVING Google Sheet
    (current edits) when no version is given, or a versioned .xlsx snapshot otherwise;
    assets come from Drive, downscaled and inlined. Video posts show the first frame.
    Unless publish is False, the page is uploaded to 00_Calendar & Docs as
    Ghedee_Social_Calendar_<id>_preview.html.

    Args:
        calendar_id: e.g. 'Q3_2026'.
        version: a snapshot version (e.g. 8); omit to read the live sheet.
        no_cache: re-download and re-encode every asset, ignoring the thumbnail cache.
        publish: also upload the HTML next to the calendar on Drive (default True).

    Returns the output path, the Drive link, and post/week/image counts.
    """
    from content_hub.social import preview
    return preview.build_preview(calendar_id, version,
                                 no_cache=no_cache, publish=publish, emit=_emit)


@mcp.tool()
def social_edit_calendar(calendar_id: str, edits: list[dict], mode: str = "live",
                         force: bool = False) -> dict:
    """Edit cells of EXISTING rows in the LIVING Google Sheet in place — the direct
    alternative to download → modify → upload. Each edit names a row by its Row ID and a
    column by header name (or a known alias); only the named cells are written, so a
    teammate editing other cells at the same time is never clobbered.

    The whole batch is validated first: if ANY edit is invalid, NOTHING is written and the
    errors are returned (no half-applied edits).

    GUARDRAILS: 'Status' is NOT editable — approval is a human-only decision made in the
    sheet. The machine-owned columns (Generated Asset Link / Est. Cost / AI Model) are
    written by generate and are refused unless force=True. Platform / Format / Visual Type
    values are validated against their allowed dropdown values.

    Args:
        calendar_id: e.g. 'Q3_2026'.
        edits: a list of {"row_id": "IG-014", "column": "Caption", "value": "New copy…"}.
            'column' accepts the header text or an alias (e.g. 'Headline' or 'Hook');
            'value' is written as USER_ENTERED (a URL becomes a link, a number stays numeric).
        mode: 'dry-run' previews the resolved cell writes without touching the sheet;
            'live' writes them. (No 'mock' — nothing is generated or spent, so dry-run is
            already the safe preview.)
        force: allow overwriting the machine-owned columns (default False).

    Returns the planned/written changes and any validation errors.
    """
    from content_hub.social import edit_ops
    return edit_ops.edit_rows(calendar_id, edits, mode=mode, force=force, emit=_emit)


@mcp.tool()
def social_add_rows(calendar_id: str, rows: list[dict], mode: str = "live") -> dict:
    """Append NEW rows to the LIVING Google Sheet in bulk — used to seed a fresh calendar
    (or add posts later) without the download/upload round-trip. Each row is a dict of
    column->value keyed by header name or alias, and MUST include a Row ID (the stable key);
    a Row ID already in the sheet, or repeated within the batch, is rejected. The batch is
    validated as a whole: if any row is invalid, nothing is written.

    GUARDRAILS: new rows default to Status='Draft' (so generate will pick them up); an
    explicit Status may only be a working state (Draft / Awaiting Asset) — an approval state
    is refused. The machine-owned columns (Generated Asset Link / Est. Cost / AI Model) are
    filled by generate and can't be set here. Platform / Format / Visual Type are validated.

    Args:
        calendar_id: e.g. 'Q3_2026'.
        rows: a list of row dicts, e.g.
            {"Row ID": "IG-001", "Date": "2026-08-01", "Platform": "Instagram",
             "Format": "Reel", "Headline": "…", "Caption": "…", "Visual Type": "AI text-to-video",
             "Prompt": "…"}.
        mode: 'dry-run' previews the rows without writing; 'live' appends them.

    Returns the planned/appended rows and any validation errors.
    """
    from content_hub.social import edit_ops
    return edit_ops.add_rows(calendar_id, rows, mode=mode, emit=_emit)


if __name__ == "__main__":
    mcp.run()
