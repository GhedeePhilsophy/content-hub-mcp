# Ghedee Content Hub — MCP server

MCP server backing the **Content Hub** Cowork workflows for The Ghedee Centre.
Planned to cover three content types — **Blog Posts**, **Social Media Calendar**,
**Emails**. **Phase 1 (this build) is the Social Calendar workflow only.**

The canonical calendar is a **living Google Sheet** (`Ghedee_Social_Calendar_<id>`,
e.g. `Ghedee_Social_Calendar_Q3_2026`) in `00_Calendar & Docs`, which the team edits
in place. The tools:

| Tool | What it does |
|---|---|
| `social_create_calendar` | Start a new calendar: create the Drive folder tree (folder named by the Calendar ID) + an empty, styled living-sheet shell in `00_Calendar & Docs`, and write a local `Ghedee_Social_Calendar_<id>_v1.xlsx` into the caller's `dest_dir` for Cowork to fill in. |
| `social_generate_media` | Read the live sheet's Draft rows → generate the missing AI images/videos → upload to Drive → write each link / cost / model / notes back **into the live sheet in place** (Sheets API — no download/re-upload). |
| `social_upload_calendar` | Create the living Google Sheet from a local `.xlsx` at `source_path` (`--replace` to overwrite). |
| `social_download_calendar` | Export the living sheet to a local `.xlsx` in the caller's `dest_dir` so Cowork can ingest current edits. |
| `social_snapshot_calendar` | Export the living sheet to the next `_v<N>.xlsx` on Drive (a frozen approval-round record). |
| `social_build_preview` | Build an HTML review page from the live sheet and publish it next to the calendar as `Ghedee_Social_Calendar_<id>_preview.html`. |

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
    rules.py              calendar naming, id→folder, aspect-ratio, Drive layout
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

Each entry is described by **`Platform`** (`Instagram` / `Facebook` / `Tiktok`) +
**`Format`** (`Post` / `Reel` / `Carousel`) — e.g. "Instagram Reel". Only rows with
**Status = Draft** and Visual Type `AI text-to-image` / `AI text-to-video` /
`AI text-to-carousel` are generated. `Recorded video of Wiah` rows are never
AI-generated — they're Wiah's own clips, so they only come from a **`Created Asset
Link`** (copied into `01_Wiah Videos`); without one the row is left alone until the
film is uploaded. Kind + aspect ratio are **derived from Visual Type**:

| Visual Type | Format | Kind | Aspect | Files |
|---|---|---|---|---|
| AI text-to-carousel | `Carousel` | carousel | 4:5 | N slides (`slide-1..N_v1.png` in a group folder) |
| AI text-to-video | `Reel` | video | 9:16 | 1 (`{RowID}_{Plat}_{Slug}_v1.mp4`) |
| AI text-to-video | `Post` | video | 16:9 | 1 (`{RowID}_{Plat}_{Slug}_v1.mp4`) |
| AI text-to-image | `Post` | image | 1:1 | 1 (`{RowID}_{Plat}_{Slug}_v1.png`) |
| Recorded video of Wiah | `Reel` | video (copied) | 9:16 | 1 in `01_Wiah Videos`, from the Created Asset only |

(`AI text-to-image` + Format `Carousel` is still accepted as a carousel for backward
compatibility.) The Row ID is the stable key: the Drive existence check matches on the
`{RowID}_` prefix, so editing a headline never orphans an already-generated file.

**Carousel prompts:** the Prompt cell holds one prompt per slide, each marked
`Slide N:` (a dash `—`/`-` is also accepted) — each is generated as its own slide:

```
Slide 1: a serene forest at dawn, mist rising through tall pines
Slide 2: a lone figure on a mountain path, seen from behind
Slide 3: sunrise breaking over a wide valley, golden light
```

The **`Slides`** column is required for a carousel and the number of prompts must
match it; a blank `Slides` or a mismatch fails the row with a clear message. Optional
extras: a trailing `Style: …` / `Palette: …` clause applies to every slide, and a
per-slide `On-image text: "…"` supplies overlay wording (the art is rendered text-free
and the words are stamped on afterward).

**Idempotency:** a row is **skipped** if its asset already exists on Drive.
**Deleting the Drive file (or a carousel's group folder) is how you request a
regeneration.**

**Bring-your-own asset:** if a single-image or single-video row's
**`Created Asset Link`** column (just after `Generated Asset Link`) is
filled, generate skips the model and instead **copies that asset** into the file
the model would have produced — an image into the `.png` slot, a video into the
`.mp4` slot (accepts a Drive share link, a plain `http(s)` URL, or a local path).
If the source can't be reached the row is marked `Failed` and the run continues.
(Carousels always generate — a single link can't fill multiple slides.)

For these rows the idempotency check is content-aware: if the copy already on
Drive matches the Created Asset (compared by MD5) it's skipped; if you **point the
Created Asset Link at a different file, the next generate re-copies** it (no need
to delete the old Drive file first).

## Three modes (every tool + CLI command)

| Mode | Drive | API/credits | Writes |
|---|---|---|---|
| `dry-run` | none | none | nothing — plans jobs + reports worst-case cost |
| `mock` | yes (safe) | none | placeholder files; upload + write-back routed to a **mock destination** and a `*.mock.xlsx` copy — production is never touched |
| `live` | yes | **spends** | real generation + upload + write-back to the working sheet |

Mock uploads go to `SOCIAL_CALENDAR_MOCK_ROOT_ID` if set, else a `_mock rehearsal`
subfolder under the calendar folder — so a rehearsal can never overwrite a real asset.

## Setup

```bash
python -m venv .venv && .venv\Scripts\Activate.ps1   # PowerShell
pip install -r requirements.txt
cp .env.example .env        # fill in OPENAI_API_KEY (+ GEMINI_API_KEY for video) + SOCIAL_CALENDAR_ROOT_ID
```

**Media keys:** images use **OpenAI `gpt-image-2`** (`OPENAI_API_KEY`; your account may
need org verification to access it); video uses **Google Veo** (`GEMINI_API_KEY`). A run
only needs the key(s) for the media types it generates — an `--only image` run never
touches the Gemini key, and vice-versa.

**Google auth (one-time, interactive):** create a Desktop-app OAuth client in
Google Cloud Console → **APIs & Services → Credentials**, enable both the **Drive
API** and the **Google Sheets API**, save the client JSON as `credentials.json`.
Authorise once so the browser consent can run and cache `token.json`:

```bash
python -m content_hub.cli auth        # grants Drive + Sheets, caches token.json
```

After that every headless run (server, `mock`, `live`) reuses `token.json`.
**Never commit** `credentials.json`, `token.json`, or `.env`.

## The calendar lifecycle

```bash
# 0. Start a brand-new calendar: Drive folders + an empty living-sheet shell, and a
#    local shell .xlsx to fill in. The Calendar ID is the Drive folder name verbatim
#    (a quarter, a date range, or a single day). --out is where the shell is written:
python -m content_hub.cli social create Q3_2026 --out ./work   # -> ./work/Ghedee_Social_Calendar_Q3_2026_v1.xlsx

# 1. Seed the living Google Sheet from a local Cowork draft (one-time), by file path:
python -m content_hub.cli social upload Q3_2026 ./work/Ghedee_Social_Calendar_Q3_2026_v1.xlsx

# 2. Generate media and write links/cost/model/notes back INTO the live sheet:
python -m content_hub.cli social generate Q3_2026 --mode dry-run   # plan + cost only
python -m content_hub.cli social generate Q3_2026 --mode mock      # rehearse (live sheet untouched)
python -m content_hub.cli social generate Q3_2026 --mode live      # spends; edits the sheet in place

# 3. Review page from the live sheet, published beside the calendar on Drive:
python -m content_hub.cli social preview Q3_2026

# 4. Pull the live sheet down for Cowork, or freeze a versioned snapshot:
python -m content_hub.cli social download Q3_2026 --out ./work   # -> ./work/Ghedee_Social_Calendar_Q3_2026.xlsx
python -m content_hub.cli social snapshot Q3_2026               # -> next _v<N>.xlsx on Drive

# generate options: --only image|video   --video-model <id>   --video-duration 30
```

`generate` reads the **living Google Sheet** and, in `live` mode, writes only the
machine-owned columns (Generated Asset Link / Est. Cost / AI Model / Notes) **in
place** via the Sheets API — so a teammate editing captions or Status at the same
time is never clobbered. `dry-run` touches nothing; `mock` writes a local
`*.mock.xlsx` instead of the live sheet.

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
        "OPENAI_API_KEY": "your-openai-key-here",
        "GEMINI_API_KEY": "your-gemini-key-here"
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
- **Images: OpenAI `gpt-image-2`.** Rendered at native aspect ratios (1:1 → 1024×1024,
  4:5 carousel → 1024×1280), `quality=high`, `moderation=low`. No crop step — the model
  renders the requested ratio directly.
- **Cost** figures come from a rough price table in `core/media.py` — a guide, not a
  bill. Images bill per output token (~$0.12–0.16/image at high quality); confirm with
  [OpenAI's image-cost calculator](https://developers.openai.com/api/docs/guides/image-generation).
  Video is per-second [Veo pricing](https://ai.google.dev/gemini-api/docs/pricing).
- **Model access / deprecations:** if images 404, confirm your OpenAI account can use
  `gpt-image-2` (org verification may be required). Google rotates Veo `-preview` names —
  on a video model-not-found error, update `DEFAULT_VIDEO_MODEL` in `core/config.py`.
- **Veo resolution/duration:** `1080p` only renders at 8s; hero clips default to
  720p/6s. Validated before spending.
- **Reference docs** (the Cowork workflow prompt and the Drive asset-structure
  layout) live in the Cowork Content Hub project, not this repo.
```
