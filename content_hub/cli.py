#!/usr/bin/env python3
"""cli — manual test harness for the Content Hub workflows.

Run the exact operations the MCP server exposes, from a terminal, so you can walk
each one through dry-run -> mock -> live before the server is deployed. Commands
are namespaced by workflow — ``<workflow> <operation>`` here mirrors the
``<workflow>_<operation>`` MCP tool name. Social is the only workflow so far;
blog and email register the same way later.

  python -m content_hub.cli auth                                    # one-time Drive consent
  python -m content_hub.cli social generate Q3_2026 6 --mode dry-run
  python -m content_hub.cli social generate Q3_2026 6 --mode mock
  python -m content_hub.cli social generate Q3_2026 6 --mode live
  python -m content_hub.cli social upload   Q3_2026 6 --mode live
  python -m content_hub.cli social download Q3_2026

Modes:
  dry-run  plan only. No Drive, no API, nothing written.
  mock     placeholder files; uploads + write-back go to a SAFE mock destination
           and a *.mock.xlsx copy. Production assets are never touched.
  live     the real thing (spends credits, writes to Drive + the working sheet).
"""

from __future__ import annotations

import argparse
import json
import sys

from .core import config
from . import social
from .social import preview


def _print_result(res: dict) -> None:
    print(json.dumps(res, indent=2, ensure_ascii=False))


# --- auth (content-agnostic Drive consent) ---------------------------------
def _register_auth(workflows) -> None:
    p = workflows.add_parser(
        "auth", help="One-time Google Drive browser consent; caches token.json.")
    p.set_defaults(func=_do_auth)


def _do_auth(_args) -> dict:
    """Run the interactive OAuth flow once so headless runs (server, mock, live)
    can reuse the cached token."""
    from .core.drive import DriveClient
    DriveClient(config.credentials_path(), config.token_path(), allow_interactive=True)
    return {"action": "authorised", "token": str(config.token_path())}


# --- social workflow -------------------------------------------------------
def _register_social(workflows) -> None:
    p = workflows.add_parser("social", help="Social Calendar workflow.")
    ops = p.add_subparsers(dest="operation", required=True)

    g = ops.add_parser("generate", help="Generate + (mock/live) upload AI visuals for Draft rows.")
    g.add_argument("calendar_id", help="e.g. Q3_2026")
    g.add_argument("version", type=int, help="Draft version number, e.g. 6")
    g.add_argument("--mode", choices=["dry-run", "mock", "live"], default="dry-run")
    g.add_argument("--only", choices=["image", "video"], help="Limit to one media type.")
    g.add_argument("--quarter-folder", help="Override the derived quarter folder, e.g. 'Q3 2026'.")
    g.add_argument("--video-model", help="Override the video model for this run "
                   "(e.g. veo-3.1-fast-generate-preview); the sheet is unchanged.")
    g.add_argument("--image-model", help="Override the image model for this run; "
                   "the sheet is unchanged.")
    g.add_argument("--video-duration", type=int, metavar="SECONDS",
                   help="Target video length in seconds (Veo chains extensions for "
                        ">8s, up to 30); the sheet is unchanged.")
    g.set_defaults(func=lambda a: social.generate_media(
        a.calendar_id, a.version, mode=a.mode, only=a.only,
        quarter_folder=a.quarter_folder, image_model=a.image_model,
        video_model=a.video_model, video_duration=a.video_duration))

    u = ops.add_parser("upload", help="Upload the local draft .xlsx to 00_Calendar & Docs.")
    u.add_argument("calendar_id")
    u.add_argument("version", type=int)
    u.add_argument("--mode", choices=["dry-run", "mock", "live"], default="dry-run")
    u.add_argument("--quarter-folder")
    u.set_defaults(func=lambda a: social.upload_calendar(
        a.calendar_id, a.version, mode=a.mode, quarter_folder=a.quarter_folder))

    d = ops.add_parser("download", help="Download the newest draft for a calendar to local.")
    d.add_argument("calendar_id")
    d.add_argument("--quarter-folder")
    d.set_defaults(func=lambda a: social.download_latest(
        a.calendar_id, quarter_folder=a.quarter_folder))

    p2 = ops.add_parser("preview", help="Build a self-contained HTML review page of the posts.")
    p2.add_argument("calendar_id")
    p2.add_argument("version", type=int, nargs="?", default=None,
                    help="Draft version to preview; omit to use the latest on Drive.")
    p2.add_argument("--out", help="Output .html path (default: alongside the calendar).")
    p2.add_argument("--quarter-folder", help="Override the derived quarter folder on "
                    "Drive, e.g. 'Q3 2026'.")
    p2.add_argument("--no-cache", action="store_true",
                    help="Re-download and re-encode every asset (ignore the thumbnail cache).")
    p2.set_defaults(func=lambda a: preview.build_preview(
        a.calendar_id, a.version, out_path=a.out,
        quarter_folder=a.quarter_folder, no_cache=a.no_cache))


def main(argv: list[str] | None = None) -> int:
    config.load_dotenv()
    ap = argparse.ArgumentParser(prog="content_hub.cli",
                                 description="Ghedee Content Hub workflows — manual runner.")
    workflows = ap.add_subparsers(dest="workflow", required=True)
    _register_auth(workflows)
    _register_social(workflows)
    # future: _register_blog(workflows), _register_email(workflows)

    args = ap.parse_args(argv)
    try:
        res = args.func(args)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    _print_result(res)
    return 1 if res.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
