# Build Instructions

**Scope note.** This project has **no compile step and no build system** — it is a Python
package run directly (as an MCP server over stdio, or via the CLI harness). "Build" here means
two things: preparing the environment, and producing the deliverable artifact, which for the
preview-simulators feature is the **self-contained HTML page**.

## Prerequisites

- **Runtime**: Python 3.10+ (the venv in this repo is used for all commands below)
- **Dependencies**: `requirements.txt` (runtime) and `requirements-dev.txt` (tests)
- **Optional**: Node.js — only for the page-contract integration check
  (`tests/page_contract.js`). Everything else runs without it.
- **Credentials**: `credentials.json` + a cached `token.json` (Google OAuth). Building a page
  reads the living sheet and the assets on Drive.
- **Environment**: `.env` with `SOCIAL_CALENDAR_ROOT_ID`. Image/video API keys are **not**
  needed — the preview generates no media.
- **System**: no special requirements. A cold build downloads ~100 assets, so expect a few
  minutes and normal network use.

## Build Steps

### 1. Install dependencies

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt      # only needed to run the tests
```

### 2. Configure environment

```powershell
cp .env.example .env       # then fill SOCIAL_CALENDAR_ROOT_ID
python -m content_hub.cli auth   # one-time interactive OAuth -> caches token.json
```

### 3. Produce the artifact

```powershell
# local build, nothing uploaded - use this for review and verification
python -m content_hub.cli social preview Q3_2026 --no-publish

# publish beside the calendar in 00_Calendar & Docs (deliberate, separate run)
python -m content_hub.cli social preview Q3_2026
```

Useful flags: `--out <path>` to write elsewhere; `--no-cache` to re-download and re-encode
every asset (a true cold build); a positional version number to build from a frozen `.xlsx`
snapshot instead of the living sheet.

### 4. Verify build success

- **Expected stderr output**:
  ```
  calendar: Q3_2026 (live) from Drive
  preview: 120 posts, 67 approved / 5 draft / 48 awaiting asset / ...
  cache: 106 reused, 0 re-encoded          <- absent when --no-cache is used
  wrote <path>\Ghedee_Social_Calendar_Q3_2026_preview.html
  ```
  Then a JSON result on stdout. **`"source"` must read `"live"`** (or `"v<n>"` for a snapshot).
- **Artifact**: `Ghedee_Social_Calendar_Q3_2026_preview.html` in the repo root (gitignored).
  Roughly **9.3 MB** for the 120-post Q3_2026 calendar.
- **Build time**: a few seconds warm (cache hit); **~2m30s cold** (`--no-cache`, 106 assets
  re-downloaded and re-encoded).
- **Acceptable**: a `cache: N reused, M re-encoded` line with M > 0 simply means assets changed
  on Drive.

## Troubleshooting

### `SOCIAL_CALENDAR_ROOT_ID is not set`
The `.env` is missing or unfilled. Copy `.env.example` and set the Drive folder id.

### `RefreshError` / auth failure
`token.json` has expired or was never created. Re-run `python -m content_hub.cli auth`. The
preview builder runs with `allow_interactive=False`, so it will not prompt — it fails instead.

### `could not find the live sheet or any .xlsx for <id>`
The calendar folder or `00_Calendar & Docs` is missing under the Social Calendar root, or the
id is wrong. Check the folder layout in README.md.

### Page builds but images are missing
Assets are resolved **solely** from the sheet's Generated Asset Link column. A blank cell, a
`Failed` value, or a link to a deleted file renders a placeholder tile — that is correct
behaviour, not a build failure. Fix the sheet, not the builder.

### Page is much larger than ~9.3 MB
Something is emitting image bytes more than once. The simulators must **clone** existing media
nodes, never re-render assets server-side (see the invariant in CLAUDE.md).
