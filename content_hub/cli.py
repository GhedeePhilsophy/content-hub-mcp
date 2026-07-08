#!/usr/bin/env python3
"""cli — manual test harness for the Content Hub workflows.

Run the exact operations the MCP server exposes, from a terminal, so you can walk
each one through dry-run -> mock -> live before the server is deployed. Commands
are namespaced by workflow — ``<workflow> <operation>`` here mirrors the
``<workflow>_<operation>`` MCP tool name. Social is the only workflow so far;
blog and email register the same way later.

  python -m content_hub.cli auth                                    # one-time Drive consent
  python -m content_hub.cli social create   Q3_2026
  python -m content_hub.cli social add      Q3_2026 @rows.json --mode dry-run
  python -m content_hub.cli social edit     Q3_2026 @edits.json --mode live
  python -m content_hub.cli social generate Q3_2026 --mode dry-run
  python -m content_hub.cli social generate Q3_2026 --mode mock
  python -m content_hub.cli social generate Q3_2026 --mode live

Everything lives on the LIVING Google Sheet — there is no local .xlsx round-trip.

Modes:
  dry-run  plan only. No Drive, no API, nothing written.
  mock     placeholder files; uploads + write-back go to a SAFE mock destination
           and a *.mock.xlsx copy. Production assets are never touched. (generate only.)
  live     the real thing (spends credits, writes to Drive + the working sheet).
"""

from __future__ import annotations

import argparse
import json
import sys

from .core import config
from . import social
from .social import preview, sheet_ops


def _print_result(res: dict) -> None:
    print(json.dumps(res, indent=2, ensure_ascii=False))


def _load_json_arg(value: str):
    """Parse a JSON CLI argument that is either inline JSON or ``@path`` to a .json file
    (used by `add`/`edit` for their list-of-dicts payloads)."""
    from pathlib import Path
    text = Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value
    return json.loads(text)


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

    g = ops.add_parser("generate", help="Generate AI visuals for the live sheet's Draft "
                       "rows and write links/cost/notes back into it in place.")
    g.add_argument("calendar_id", help="e.g. Q3_2026")
    g.add_argument("--mode", choices=["dry-run", "mock", "live"], default="dry-run")
    g.add_argument("--only", choices=["image", "video"], help="Limit to one media type.")
    g.add_argument("--video-model", help="Override the video model for this run "
                   "(e.g. veo-3.1-fast-generate-preview); the sheet is unchanged.")
    g.add_argument("--image-model", help="Override the image model for this run; "
                   "the sheet is unchanged.")
    g.add_argument("--video-duration", type=int, metavar="SECONDS",
                   help="Target video length in seconds (Veo chains extensions for "
                        ">8s, up to 30); the sheet is unchanged.")
    g.set_defaults(func=lambda a: social.generate_media(
        a.calendar_id, mode=a.mode, only=a.only, image_model=a.image_model,
        video_model=a.video_model, video_duration=a.video_duration))

    c = ops.add_parser("create", help="Initialise a new calendar: Drive folders + an empty "
                       "living Google Sheet shell (fill it with `add`).")
    c.add_argument("calendar_id", help="A quarter (Q3_2026), a date range, or a single day; "
                   "it is the Drive folder name.")
    c.add_argument("--replace", action="store_true",
                   help="Recreate an empty shell even if a live sheet exists (trashes the old).")
    c.set_defaults(func=lambda a: sheet_ops.create(a.calendar_id, replace=a.replace))

    ad = ops.add_parser("add", help="Append new rows to the live sheet in bulk "
                        "(each row is a {header: value} dict; a Row ID is required).")
    ad.add_argument("calendar_id")
    ad.add_argument("rows", help="Inline JSON list of row dicts, or @path to a .json file.")
    ad.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    ad.set_defaults(func=lambda a: social.add_rows(
        a.calendar_id, _load_json_arg(a.rows), mode=a.mode))

    ed = ops.add_parser("edit", help="Edit cells of existing rows in the live sheet "
                        "(each edit is {row_id, column, value}).")
    ed.add_argument("calendar_id")
    ed.add_argument("edits", help="Inline JSON list of edit dicts, or @path to a .json file.")
    ed.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    ed.add_argument("--force", action="store_true",
                    help="Allow overwriting the machine-owned columns (Generated Asset Link "
                         "/ Est. Cost / AI Model).")
    ed.set_defaults(func=lambda a: social.edit_rows(
        a.calendar_id, _load_json_arg(a.edits), mode=a.mode, force=a.force))

    p2 = ops.add_parser("preview", help="Build a self-contained HTML review page of the posts.")
    p2.add_argument("calendar_id")
    p2.add_argument("version", type=int, nargs="?", default=None,
                    help="Draft version to preview; omit to use the latest on Drive.")
    p2.add_argument("--out", help="Output .html path (default: alongside the calendar).")
    p2.add_argument("--no-cache", action="store_true",
                    help="Re-download and re-encode every asset (ignore the thumbnail cache).")
    p2.add_argument("--no-publish", action="store_true",
                    help="Build locally only; don't upload the HTML to Drive.")
    p2.set_defaults(func=lambda a: preview.build_preview(
        a.calendar_id, a.version, out_path=a.out,
        no_cache=a.no_cache, publish=not a.no_publish))


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
