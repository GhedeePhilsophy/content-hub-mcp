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
def social_generate_media(calendar_id: str, version: int, mode: str = "dry-run",
                          only: str | None = None,
                          quarter_folder: str | None = None,
                          image_model: str | None = None,
                          video_model: str | None = None,
                          video_duration: int | None = None) -> dict:
    """Generate the missing AI visuals for a Social Calendar draft, then (mock/live)
    upload them to Google Drive and write each Drive link + cost back into the sheet.

    Reads the local working copy Ghedee_Social_Calendar_<calendar_id>_v<version>.xlsx.
    Only rows with Status=Draft and Visual Type 'AI text-to-image' / 'AI text-to-video'
    are generated. A row is SKIPPED if its asset is already on Drive — delete the
    Drive file to force a regeneration. 'Recorded video of Wiah' rows are never
    generated. Aspect ratio is derived per row (image 1:1, carousel 3:4, video 16:9).
    A row that fails is marked 'Failed' in its asset-link cell with the reason in Notes.

    Args:
        calendar_id: e.g. 'Q3_2026' (the id segment of the filename).
        version: the draft version number, e.g. 6.
        mode: 'dry-run' (plan + cost only), 'mock' (safe rehearsal), or 'live'.
        only: optionally limit to 'image' or 'video'.
        quarter_folder: override the derived Drive quarter folder (e.g. 'Q3 2026'),
            required when calendar_id is a month or date range rather than a quarter.
        video_model: override the video model for this run only (e.g.
            'veo-3.1-fast-generate-preview' to dodge a quota); the sheet is unchanged.
        image_model: override the image model for this run only; the sheet is unchanged.
        video_duration: target video length in seconds; Veo chains extensions for
            clips over 8s (up to 30). The sheet is unchanged.

    Returns a summary: per-row action (would-generate / generated / skipped-existing /
    failed), Drive links, per-row and total estimated cost, and the written-back file.
    """
    return social.generate_media(calendar_id, version, mode=mode, only=only,
                                 quarter_folder=quarter_folder, image_model=image_model,
                                 video_model=video_model, video_duration=video_duration,
                                 emit=_emit)


@mcp.tool()
def social_upload_calendar(calendar_id: str, version: int, mode: str = "dry-run",
                           quarter_folder: str | None = None) -> dict:
    """Upload a local Social Calendar draft .xlsx to its quarter's
    '00_Calendar & Docs' folder on Google Drive (a synced reference copy — the
    working file stays in the Content Hub project).

    Args:
        calendar_id: e.g. 'Q3_2026'.
        version: the draft version number, e.g. 6.
        mode: 'dry-run' (report destination only), 'mock' (safe sandbox), or 'live'.
        quarter_folder: override the derived quarter folder when needed.
    """
    return social.upload_calendar(calendar_id, version, mode=mode,
                                  quarter_folder=quarter_folder, emit=_emit)


@mcp.tool()
def social_build_preview(calendar_id: str, version: int,
                         quarter_folder: str | None = None,
                         no_cache: bool = False) -> dict:
    """Build a self-contained HTML review page of a Social Calendar's posts, each
    rendered as a mockup in its platform's chrome (Instagram / Facebook / TikTok),
    grouped by week, plus an Instagram profile-grid view. All post assets are read
    from Google Drive, downscaled, and inlined, so the result is a single portable
    .html file for review/approval. Video posts show the clip's first frame.

    Args:
        calendar_id: e.g. 'Q3_2026'.
        version: the draft version number, e.g. 8.
        quarter_folder: override the derived quarter folder on Drive.
        no_cache: re-download and re-encode every asset, ignoring the thumbnail cache
            (which otherwise reuses assets whose Drive md5 is unchanged).

    Returns the output path and post/week/image counts.
    """
    from content_hub.social import preview
    return preview.build_preview(calendar_id, version,
                                 quarter_folder=quarter_folder, no_cache=no_cache,
                                 emit=_emit)


@mcp.tool()
def social_download_latest(calendar_id: str,
                           quarter_folder: str | None = None) -> dict:
    """Download the highest-version Social Calendar draft for this calendar from
    Google Drive into the local Content Hub working folder, so Cowork can ingest
    the latest edits and notes.

    Args:
        calendar_id: e.g. 'Q3_2026'.
        quarter_folder: override the derived quarter folder when needed.
    """
    return social.download_latest(calendar_id, quarter_folder=quarter_folder, emit=_emit)


if __name__ == "__main__":
    mcp.run()
