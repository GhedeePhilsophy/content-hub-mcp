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
        video_model / image_model: override the model for this run only (e.g.
            'veo-3.1-fast-generate-preview'); the model actually used is written to the sheet.
        video_duration: target video length in seconds; Veo chains extensions past 8s (to 30).

    Returns a per-row summary, links, costs, and (live) the count of cells updated.
    """
    return social.generate_media(calendar_id, mode=mode, only=only,
                                 image_model=image_model, video_model=video_model,
                                 video_duration=video_duration, emit=_emit)


@mcp.tool()
def social_create_calendar(calendar_id: str, dest_dir: str,
                           replace: bool = False) -> dict:
    """Initialise a brand-new Social Calendar (call this to START a new calendar). Creates
    the Google Drive folder tree (the calendar folder + 00_Calendar & Docs, 02_AI Visuals/
    Images + /Video, 03_Carousels), creates the LIVING Google Sheet as an empty, styled
    header-only shell in 00_Calendar & Docs, and writes a local shell .xlsx
    (Ghedee_Social_Calendar_<id>_v1.xlsx) into dest_dir for Cowork to fill in.

    calendar_id may be a quarter (e.g. 'Q3_2026'), a date range, or a single day — it is NOT
    required to be a quarter. Whatever it is, it becomes the Drive folder name verbatim.

    Args:
        calendar_id: the new calendar's id (a quarter, a date range, or a single day); this
            is used verbatim as the Drive folder name.
        dest_dir: REQUIRED — the absolute path to your (Cowork's) working directory where the
            shell .xlsx should be written. The server can't reach into your sandbox, so pass
            the folder you want the file in.
        replace: recreate an empty shell even if a live sheet already exists (trashes the old).

    Returns the folder name, the living sheet name/id/link, and the local shell .xlsx path.
    """
    from content_hub.social import sheet_ops
    return sheet_ops.create(calendar_id, dest_dir, replace=replace, emit=_emit)


@mcp.tool()
def social_upload_calendar(calendar_id: str, source_path: str,
                           replace: bool = False) -> dict:
    """Create the LIVING Google Sheet (Ghedee_Social_Calendar_<id>) in 00_Calendar & Docs
    from a local .xlsx at source_path. The living sheet is the canonical calendar the team
    edits in place. Fails if it already exists unless replace=True (which trashes the old
    one and recreates it from this .xlsx).

    Args:
        calendar_id: e.g. 'Q3_2026'.
        source_path: REQUIRED — the absolute path to the .xlsx to upload (in your/Cowork's
            working directory). The local filename can be anything; the living sheet is
            always named Ghedee_Social_Calendar_<id>.
        replace: overwrite an existing live sheet from this .xlsx.
    """
    from content_hub.social import sheet_ops
    return sheet_ops.upload(calendar_id, source_path, replace=replace, emit=_emit)


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
def social_snapshot_calendar(calendar_id: str) -> dict:
    """Export the living Google Sheet to the next versioned .xlsx snapshot
    (Ghedee_Social_Calendar_<id>_v<N>.xlsx) in 00_Calendar & Docs — a frozen
    approval-round record. Returns the new version, filename, and Drive link.
    """
    from content_hub.social import sheet_ops
    return sheet_ops.snapshot(calendar_id, emit=_emit)


@mcp.tool()
def social_download_calendar(calendar_id: str, dest_dir: str) -> dict:
    """Export the living Google Sheet to a local .xlsx (Ghedee_Social_Calendar_<id>.xlsx)
    in dest_dir, so Cowork can ingest the current edits and notes.

    Args:
        calendar_id: e.g. 'Q3_2026'.
        dest_dir: REQUIRED — the absolute path to your (Cowork's) working directory where the
            .xlsx should be written. The server can't reach into your sandbox, so pass the
            folder you want the file in.
    """
    from content_hub.social import sheet_ops
    return sheet_ops.download(calendar_id, dest_dir, emit=_emit)


if __name__ == "__main__":
    mcp.run()
