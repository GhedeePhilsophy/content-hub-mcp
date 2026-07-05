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
                          quarter_folder: str | None = None,
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
        quarter_folder: override the derived Drive quarter folder (e.g. 'Q3 2026').
        video_model / image_model: override the model for this run only (e.g.
            'veo-3.1-fast-generate-preview'); the model actually used is written to the sheet.
        video_duration: target video length in seconds; Veo chains extensions past 8s (to 30).

    Returns a per-row summary, links, costs, and (live) the count of cells updated.
    """
    return social.generate_media(calendar_id, mode=mode, only=only,
                                 quarter_folder=quarter_folder, image_model=image_model,
                                 video_model=video_model, video_duration=video_duration,
                                 emit=_emit)


@mcp.tool()
def social_upload_calendar(calendar_id: str, version: int,
                           quarter_folder: str | None = None,
                           replace: bool = False) -> dict:
    """Create the LIVING Google Sheet (Ghedee_Social_Calendar_<id>) from a local
    Ghedee_Social_Calendar_<id>_v<version>.xlsx in 00_Calendar & Docs. The living sheet
    is the canonical calendar the team edits in place. Fails if it already exists unless
    replace=True (which trashes the old one and recreates it from this .xlsx).

    Args:
        calendar_id: e.g. 'Q3_2026'.
        version: the local draft version number to create the sheet from, e.g. 8.
        quarter_folder: override the derived quarter folder when needed.
        replace: overwrite an existing live sheet from this .xlsx.
    """
    from content_hub.social import sheet_ops
    return sheet_ops.upload(calendar_id, version, quarter_folder=quarter_folder,
                            replace=replace, emit=_emit)


@mcp.tool()
def social_build_preview(calendar_id: str, version: int | None = None,
                         quarter_folder: str | None = None,
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
        quarter_folder: override the derived quarter folder on Drive.
        no_cache: re-download and re-encode every asset, ignoring the thumbnail cache.
        publish: also upload the HTML next to the calendar on Drive (default True).

    Returns the output path, the Drive link, and post/week/image counts.
    """
    from content_hub.social import preview
    return preview.build_preview(calendar_id, version, quarter_folder=quarter_folder,
                                 no_cache=no_cache, publish=publish, emit=_emit)


@mcp.tool()
def social_snapshot_calendar(calendar_id: str, quarter_folder: str | None = None) -> dict:
    """Export the living Google Sheet to the next versioned .xlsx snapshot
    (Ghedee_Social_Calendar_<id>_v<N>.xlsx) in 00_Calendar & Docs — a frozen
    approval-round record. Returns the new version, filename, and Drive link.
    """
    from content_hub.social import sheet_ops
    return sheet_ops.snapshot(calendar_id, quarter_folder=quarter_folder, emit=_emit)


@mcp.tool()
def social_download_calendar(calendar_id: str,
                             quarter_folder: str | None = None) -> dict:
    """Export the living Google Sheet to a local .xlsx (Ghedee_Social_Calendar_<id>.xlsx)
    in the Content Hub working folder, so Cowork can ingest the current edits and notes.

    Args:
        calendar_id: e.g. 'Q3_2026'.
        quarter_folder: override the derived quarter folder when needed.
    """
    from content_hub.social import sheet_ops
    return sheet_ops.download(calendar_id, quarter_folder=quarter_folder, emit=_emit)


if __name__ == "__main__":
    mcp.run()
