# Ghedee Content Hub — MCP server

MCP server backing the **Content Hub** Cowork workflows for The Ghedee Centre.
Planned to cover three content types — **Blog Posts**, **Social Media Calendar**,
**Emails**. **Phase 1 (this build) is the Social Calendar workflow only.**

The canonical calendar is a **living Google Sheet** (`Ghedee_Social_Calendar_<id>`,
e.g. `Ghedee_Social_Calendar_Q3_2026`) in `00_Calendar & Docs`, which the team edits
in place. The tools:

| Tool | What it does |
|---|---|
| `social_create_calendar` | Start a new calendar: create the Drive folder tree (folder named by the Calendar ID) + an empty, styled living-sheet shell in `00_Calendar & Docs`. Cowork fills it in with `social_add_rows` — no local file. |
| `social_add_rows` | Append **new** rows to the live sheet in bulk (this is how you seed a fresh calendar). Each row is keyed by header name and must carry a Row ID; new rows default to Status `Draft`, and an approval status can't be set. |
| `social_edit_calendar` | Edit cells of **existing** rows in the live sheet in place. Edits name a row by **Row ID** and a column by header name; only the named cells are written. Status is not editable (human-only approval); the machine-owned columns are `force`-gated. |
| `social_describe_calendar` | Report the live sheet's shape — its columns (exact header text, which are editable and why not, allowed values) and its rows (Row ID, status, platform, …). Read-only, no `mode`; call it before `edit`/`add` when the column names or Row IDs aren't certain. |
| `social_generate_media` | Read the live sheet's Draft rows → generate the missing AI images/videos → upload to Drive → write each link / cost / model / notes back **into the live sheet in place** (Sheets API). |
| `social_build_preview` | Build an HTML review page from the live sheet and publish it next to the calendar as `Ghedee_Social_Calendar_<id>_preview.html`. |
| `social_export_calendar` | Write a scheduler's bulk-import file from the live sheet — **Metricool** or **Publer** CSV. Exports finished rows only, resolves each Drive asset to a fetchable URL, and expands carousels into their slides. |

Direct Metricool *publishing* (via their API) is planned for a later phase; today the
handoff is the export file above.

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
    exporters/            bulk-import files for external schedulers
      __init__.py         sheet → neutral Post objects + the Drive media-URL rule
      metricool.py        Metricool's CSV (one file, per-platform boolean columns)
      publer.py           Publer's CSV (one file PER platform; Drive share links)
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
If the source can't be reached the row is marked `Failed` and the run continues. A
**carousel** row's Created Asset Link points at a **Drive folder**: its images, in
**alphabetical order**, become the slides. Both that source folder and the existing
destination folder must hold exactly `Slides` images, or the row errors (a stale
destination left over from a different slide count is caught before anything is written).

For these rows the idempotency check is content-aware (compared by MD5): if the copy
already on Drive matches the Created Asset it's skipped, and pointing the Created Asset
Link at a different file makes the next generate **re-copy** it (no need to delete the
old Drive file first). For a folder-sourced carousel this is per-slide — only the slides
whose source image changed are re-uploaded.

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
# 0. Start a brand-new calendar: Drive folders + an empty living-sheet shell. The
#    Calendar ID is the Drive folder name verbatim (a quarter, a date range, or a day):
python -m content_hub.cli social create Q3_2026

# 1. Seed the living Google Sheet by appending rows directly (bulk). rows.json is a
#    JSON list of {header: value} dicts, each with a Row ID; dry-run previews first:
python -m content_hub.cli social add Q3_2026 @rows.json --mode dry-run
python -m content_hub.cli social add Q3_2026 @rows.json --mode live

# 1b. Later tweaks: edit cells of existing rows in place (by Row ID + column):
python -m content_hub.cli social edit Q3_2026 '[{"row_id":"IG-014","column":"Caption","value":"New copy"}]' --mode live

# 2. Generate media and write links/cost/model/notes back INTO the live sheet:
python -m content_hub.cli social generate Q3_2026 --mode dry-run   # plan + cost only
python -m content_hub.cli social generate Q3_2026 --mode mock      # rehearse (live sheet untouched)
python -m content_hub.cli social generate Q3_2026 --mode live      # spends; edits the sheet in place

# 3. Review page from the live sheet, published beside the calendar on Drive:
python -m content_hub.cli social preview Q3_2026

# 4. Hand the approved round to a scheduler as a bulk-import file:
python -m content_hub.cli social export Q3_2026 --target metricool
python -m content_hub.cli social export Q3_2026 --target publer     # one file per platform

# generate options: --only image|video   --video-model <id>   --video-duration 30
```

`generate` reads the **living Google Sheet** and, in `live` mode, writes only the
machine-owned columns (Generated Asset Link / Est. Cost / AI Model / Notes) **in
place** via the Sheets API — so a teammate editing captions or Status at the same
time is never clobbered. `dry-run` touches nothing; `mock` writes a local
`*.mock.xlsx` instead of the live sheet.

## Editing the live sheet directly

All calendar changes go straight to the **living Google Sheet** — there is no local
`.xlsx` to download, edit, and re-upload. Two tools edit it in place over the same Sheets
API `generate` uses, so only the cells named are touched (concurrent human edits survive):

- **`social_edit_calendar`** — change cells of existing rows. Each edit names a row by
  its **Row ID** and a column by header name (or a known alias, e.g. `Hook` → Headline):
  `{"row_id": "IG-014", "column": "Caption", "value": "New copy…"}`. The batch is
  validated as a whole — if any edit is invalid, **nothing** is written and the errors
  come back, so the sheet is never left half-edited.
- **`social_add_rows`** — append new rows in bulk to seed a calendar. Each row is a
  `{header: value}` dict and must carry a Row ID; rows land after the last used row.
- **`social_describe_calendar`** — look before you write. Reports the sheet's **columns**
  (the exact header text to pass as `column`, whether each is editable and why not, and
  the `allowed_values` of the constrained ones) and its **rows** (Row ID, date, platform,
  format, visual type, status, headline, whether an asset link exists). Read-only, so it
  takes no `mode`. Call it whenever the sheet's column names or Row IDs aren't certain —
  a guessed column name is rejected, and since the batch is all-or-nothing it discards
  every other edit with it.

`edit` and `add` take `mode` = `dry-run` (preview the resolved writes, touch nothing) or `live`
(write). There's no `mock` here — nothing is generated or spent, so `dry-run` already is
the safe rehearsal. **Guardrails** (why this is a schema-aware tool, not a raw Sheets
connector): **Status is never editable** — approval is a human-only decision; new rows
default to `Draft` and an approval status can't be set. The **machine-owned columns**
(Generated Asset Link / Est. Cost / AI Model) are written by `generate` — `edit` refuses
them unless `force=true`, `add` refuses them outright. Constrained columns (Platform /
Format / Visual Type) are validated against their allowed values, and an unknown column
or Row ID is rejected with a clear message.

## The review page (and the platform simulators)

`social preview` builds a **single self-contained HTML file** from the living sheet and
publishes it beside the calendar in `00_Calendar & Docs`. Every asset is read from Drive,
downscaled, and inlined as a data URI, so the page opens offline and can be shared as one
file. A per-calendar cache keyed by Drive md5 means a rebuild only re-fetches assets that
actually changed. `--no-publish` builds locally without uploading; `--no-cache` re-encodes
everything.

The page has two views:

**Review feed** (default) — every post in its platform's chrome, inside a status-framed
card carrying the Row ID, date, format, a status pill and copy-caption / Sheet / Asset
buttons, grouped by week with an approval roll-up per week. Chips filter by platform,
Reels, Carousels and status. Served through the Apps Script web app, the status pill is a
live dropdown that writes straight back to the sheet.

**Simulator** — the *Simulator* chip opens a full-screen overlay showing the calendar as
each platform's real feed, inside a phone chassis (toggleable via *Phone frame*):

| Platform | Surfaces |
|---|---|
| Instagram | **Feed** · **Profile** grid · **Reels** |
| Facebook | Feed (caption above media, reaction bar) |
| TikTok | Full-bleed 9:16, snap-scrolled, with the action rail |

Posts run newest-first, the review page's status filter carries through (mirrored as the
*Showing* dropdown in the overlay header), and **hovering** a post reveals its Row ID, date
and status. Engagement counts are **simulated** — the calendar holds no performance data —
and are derived deterministically from the Row ID, so they never change between rebuilds.
Adjust their scale via `_ENGAGE_LIKES` / `_ENGAGE_VIEWS` in `social/preview.py`.

Video posts show the clip's first frame with a play button that **opens it on Drive** in a
new tab. There is no inline playback, and that is deliberate — both routes were tried and
measured. A native `<video>` cannot load the file: Drive answers a browser's `Origin: null`
(any page opened from disk) with `403` and no `Access-Control-Allow-Origin`, and blocks the
non-CORS path with `Cross-Origin-Resource-Policy: same-site`. Drive's own `/preview` player
does work, but it is a player *page* and draws a toolbar above the picture, which reads as a
black band across the top of a feed post. A paused feed shows stills anyway.

The simulator adds **no image bytes** to the file: each surface is built in the browser by
cloning media nodes the review feed already contains, so every asset is embedded exactly
once. There is no separate "IG Grid" view — it is now the Instagram simulator's Profile tab.

## Exporting to a scheduler

`social export` turns the finished part of the calendar into a scheduler's bulk-import
file. Two targets exist — **Metricool** and **Publer** (`--target`, default `metricool`).
`content_hub/social/exporters/` is a registry, so a new one is a module exposing `NAME`,
`EXTENSION` and `write(posts, out, **opts)` — it receives fully-resolved `Post` objects
and never touches Drive or the sheet.

```bash
python -m content_hub.cli social export Q3_2026                         # metricool, Approved, drafts
python -m content_hub.cli social export Q3_2026 --target publer         # Publer, per-platform files
python -m content_hub.cli social export Q3_2026 --statuses Approved,Draft
python -m content_hub.cli social export Q3_2026 --schedule              # live scheduled posts
```

Only rows whose Status says they're finished are exported (**Approved** by default —
`--statuses` to widen). Posts are written as scheduler **drafts** unless `--schedule`,
so an import can never auto-publish before someone confirms the media resolved. A row
with no asset, or a carousel whose Drive folder disagrees with its `Slides` count, is
**skipped and reported** rather than exported broken.

Times are exported verbatim from `Time (PT)`, so the destination account's timezone must
be Pacific or the whole calendar lands hours off.

### The Drive media-URL rule

The export is only as good as the URL it hands over, and the right form **depends on the
scheduler** — specifically whether it *fetches* the URL for bytes or *parses* it as a
Drive share link and resolves the file through its own Google integration. The forms, and
what each returns to an anonymous fetcher (all measured):

| URL form (`link_style`) | Behaviour |
|---|---|
| `…/file/d/<id>/view?usp=sharing` (`share`) | `text/html` viewer page to a fetcher — **useless if fetched**, but the canonical share link a Drive *integration* wants. **Publer** uses this. |
| `drive.usercontent.google.com/download?id=<id>&export=download` (`download`) | **200 with the true content-type**, images and video alike — the answer for anything that fetches bytes. |
| `drive.google.com/uc?export=download&id=<id>` (`uc`) | **303 with an empty body**; a fetcher that doesn't follow redirects sees nothing. |
| `lh3.googleusercontent.com/d/<id>` (`lh3`) | 200 — but for a **video** it serves a poster JPEG, so the post silently publishes as a still. Avoid where video is possible. |

Each target sets its own default (`LINK_STYLE`); override with `--link-style`. Whichever
form is used, the assets must be **link-shared** ("Anyone with the link") or nothing
resolves — the exporter never changes Drive permissions, that's a human decision — and if
sharing is later tightened, every already-scheduled post loses its media.

### Publer specifics

Publer's CSV has **no platform column**: an import applies every row to the *same*
accounts picked in the import wizard. Because the calendar carries different copy per
platform, `social export --target publer` writes **one file per platform**
(`…_publer_instagram.csv`, `…_publer_facebook.csv`, …) — import each against the matching
account. Media go in a single comma-separated column (carousels expand there), alt text
uses `||`, and the IG first-comment hashtags map to Publer's `Comment(s)` column (Facebook
files omit them). Publer has no draft flag in the file — drafts-vs-scheduled is chosen in
the wizard — so `--schedule` only affects the summary for this target.

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
