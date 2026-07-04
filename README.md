# Ghedee Content Hub — MCP server

MCP server backing the **Content Hub** Cowork workflows for The Ghedee Centre.
Planned to cover three content types — **Blog Posts**, **Social Media Calendar**,
**Emails**. **Phase 1 (this build) is the Social Calendar workflow only.**

The Social Calendar is an Excel workbook Cowork produces
(`Ghedee_Social_Calendar_{CalendarID}_v{N}.xlsx`, e.g.
`Ghedee_Social_Calendar_Q3_2026_v6.xlsx`). The server reads that sheet as the
**source of truth** and offers three tools:

| Tool | What it does |
|---|---|
| `social_generate_media` | Read Draft rows → generate the missing AI images/videos → upload to Drive (route-by-type) → write each Drive link + cost back into the sheet. |
| `social_upload_calendar` | Upload a local draft `.xlsx` to its quarter's `00_Calendar & Docs/` on Drive. |
| `social_download_latest` | Download the newest draft for a calendar from Drive to the local working folder. |

Metricool publishing is planned for a later phase.

## Architecture

Three content types are planned (Blog Posts, Social Calendar, Emails). Their
**common thread** — generate AI images/video, push files to a specific Drive
folder, pull files back for Cowork — lives in `core/`. Each content type is its
own package on top; **social is the only one built so far.**

```
server.py                 thin MCP tools (FastMCP, stdio)
content_hub/
  core/                   ← the common thread, content-agnostic
    config.py             env / Google credentials / paths / brand + model defaults
    media.py              generate images + video (the AI-image primitive)
    drive.py              push to a Drive folder / pull latest / exists-check
  social/                 ← workflow #1 (blog/ and email/ become siblings)
    rules.py              calendar naming, quarter→folder, aspect-ratio, Drive layout
    calendar.py           read the .xlsx → jobs; write Drive link + cost back
    workflow.py           orchestrator: generate → push → writeback (the 3 operations)
  cli.py                  manual dry/mock/live test harness for the same 3 operations
```

The `core` engine never writes to stdout and never calls `sys.exit` — required for
an MCP stdio server, where stdout is the protocol channel. All progress goes to
stderr; tools return structured results. **Adding a workflow** = a new package
(e.g. `blog/`) that parses its own input format, defines its own Drive layout, and
reuses `core.media` + `core.drive` for the three shared primitives.

## How a calendar row becomes media

Only rows with **Status = Draft** and Visual Type `AI text-to-image` /
`AI text-to-video` are generated. `Recorded video of Wiah` rows are left alone
(they need a film shoot). Aspect ratio is **derived per row** (Visual Type first,
Format second — the `Format` column alone is ambiguous):

| Visual Type | Format | Kind | Aspect | Files |
|---|---|---|---|---|
| AI text-to-video | any | video | 16:9 | 1 (`{RowID}_{Plat}_{Slug}_v1.mp4`) |
| AI text-to-image | `Carousel` | carousel | 3:4 | N slides (`slide-1..N_v1.png` in a group folder) |
| AI text-to-image | anything else | image | 1:1 | 1 (`{RowID}_{Plat}_{Slug}_v1.png`) |

Carousels read a **`Slides`** column for the slide count (defaults to 4). The
Row ID is the stable key: the Drive existence check matches on the `{RowID}_`
prefix, so editing a hook never orphans an already-generated file.

**Idempotency:** a row is **skipped** if its asset already exists on Drive.
**Deleting the Drive file (or a carousel's group folder) is how you request a
regeneration.**

## Three modes (every tool + CLI command)

| Mode | Drive | API/credits | Writes |
|---|---|---|---|
| `dry-run` | none | none | nothing — plans jobs + reports worst-case cost |
| `mock` | yes (safe) | none | placeholder files; upload + write-back routed to a **mock destination** and a `*.mock.xlsx` copy — production is never touched |
| `live` | yes | **spends** | real generation + upload + write-back to the working sheet |

Mock uploads go to `SOCIAL_CALENDAR_MOCK_ROOT_ID` if set, else a `_mock rehearsal`
subfolder under the quarter — so a rehearsal can never overwrite a real asset.

## Setup

```bash
python -m venv .venv && .venv\Scripts\Activate.ps1   # PowerShell
pip install -r requirements.txt
cp .env.example .env        # fill in GEMINI_API_KEY + SOCIAL_CALENDAR_ROOT_ID
```

**Drive auth (one-time, interactive):** create a Desktop-app OAuth client in
Google Cloud Console → **APIs & Services → Credentials**, enable the **Drive API**,
save the JSON as `credentials.json`. Authorise once from a terminal so the browser
consent can run and cache `token.json`:

```bash
python -m content_hub.cli auth        # opens the browser, caches token.json
```

After that every headless run (server, `mock`, `live`) reuses `token.json`.
**Never commit** `credentials.json`, `token.json`, or `.env`.

## Manual runs (do this before deploying the server)

Walk each operation dry-run → mock → live. Commands are namespaced by workflow
(`social <operation>`), mirroring the `social_<operation>` MCP tool names:

```bash
# Plan + total cost, no spend, no Drive:
python -m content_hub.cli social generate Q3_2026 6 --mode dry-run

# Full pipeline with placeholder files, safe mock Drive + a *.mock.xlsx copy:
python -m content_hub.cli social generate Q3_2026 6 --mode mock

# Real generation + upload + write-back (spends credits):
python -m content_hub.cli social generate Q3_2026 6 --mode live

# Other operations:
python -m content_hub.cli social upload   Q3_2026 6 --mode live
python -m content_hub.cli social download Q3_2026

# Options: --only image|video   --quarter-folder "Q3 2026"
```

`--quarter-folder` is needed only when the Calendar ID isn't a quarter (e.g. a
month or a date range) and so can't be mapped to a `Q# YYYY` folder automatically.

## Run as an MCP server

```bash
python server.py        # serves over stdio
```

Register it as a local MCP/connector in Claude Cowork — copy
`cowork-mcp-config.example.json`, fill in the absolute paths and env values:

```json
{
  "mcpServers": {
    "ghedee-content-hub": {
      "command": "…\\content-hub-mcp\\.venv\\Scripts\\python.exe",
      "args": ["…\\content-hub-mcp\\server.py"],
      "env": {
        "SOCIAL_CALENDAR_ROOT_ID": "your-social-calendar-folder-id",
        "GEMINI_API_KEY": "your-key-here"
      }
    }
  }
}
```

`command` should point at the venv's Python (so the deps resolve). The `env` block
is optional — the server also reads `.env` on startup — but keeping the connector
self-contained avoids surprises. The three `social_*` tools then drive the same
workflow, each taking a `mode` argument. Do the one-time Drive consent first
(above) so `token.json` exists before Cowork launches the server headless.

## Notes
- **Cost** figures come from a rough price table in `core/media.py` — a guide, not
  a bill. Confirm against current [Google pricing](https://ai.google.dev/gemini-api/docs/pricing).
- **Model deprecations:** Google rotates `-preview` names. On a model-not-found
  error, update `DEFAULT_IMAGE_MODEL` / `DEFAULT_VIDEO_MODEL` in `core/config.py`.
- **Veo resolution/duration:** `1080p` only renders at 8s; hero clips default to
  720p/6s. Validated before spending.
- **Reference docs** (the Cowork workflow prompt and the Drive asset-structure
  layout) live in the Cowork Content Hub project, not this repo.
```
