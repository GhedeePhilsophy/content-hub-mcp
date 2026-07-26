# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# AI-DLC Project Rules
See full development lifecycle guidelines in: ./aws-aidlc-rules/aws-aidlc-rules/core-workflow.md

## What this is

An MCP server (FastMCP over **stdio**) backing the **Content Hub** Cowork workflows for The Ghedee Centre. Three content types are planned (Blog Posts, Social Calendar, Emails); **only the Social Calendar workflow is built** — everything under `content_hub/social/`.

The canonical calendar is a **living Google Sheet** (`Ghedee_Social_Calendar_<id>`) that the team edits in place. The tools read and write that sheet directly over the Sheets API — there is **no local `.xlsx` round-trip**. See [README.md](README.md) for the full workflow narrative, the Drive folder layout, and the per-tool guardrails; this file is the orientation, the README is the reference.

## Commands

```powershell
python -m venv .venv && .venv\Scripts\Activate.ps1     # PowerShell
pip install -r requirements.txt
cp .env.example .env      # fill OPENAI_API_KEY (+ GEMINI_API_KEY for video) + SOCIAL_CALENDAR_ROOT_ID

python -m content_hub.cli auth      # one-time interactive Google OAuth → caches token.json
python server.py                    # run the MCP server over stdio

# CLI harness — same operations the MCP tools call, for manual dry/mock/live testing:
python -m content_hub.cli social create Q3_2026
python -m content_hub.cli social add Q3_2026 @rows.json --mode dry-run
python -m content_hub.cli social edit Q3_2026 '[{"row_id":"IG-014","column":"Caption","value":"…"}]' --mode live
python -m content_hub.cli social generate Q3_2026 --mode dry-run    # dry-run | mock | live
python -m content_hub.cli social audit Q3_2026 --mode mock          # asset+caption compliance audit
python -m content_hub.cli social preview Q3_2026
python -m content_hub.cli social export Q3_2026 --target metricool  # or --target publer
```

Dev/test deps (property tests for the pure specs/audit logic) are separate:
`pip install -r requirements-dev.txt && pytest tests/`.

There is **no test suite** and no lint/build config — validate changes by running the CLI harness in `--mode dry-run` (plans + costs, touches nothing) or `--mode mock` (safe rehearsal to a mock Drive destination).

## The three modes (every tool + CLI command)

Every operation takes `mode`:
- **`dry-run`** — plan only. No Drive, no API/credits, nothing written.
- **`mock`** — placeholder files; uploads + write-back routed to a SAFE mock destination (`SOCIAL_CALENDAR_MOCK_ROOT_ID`, or a `_mock rehearsal` subfolder). Production is never touched.
- **`live`** — the real run: spends credits, writes to Drive + the living sheet.

`social_edit_calendar` / `social_add_rows` have no `mock` (nothing is generated or spent, so `dry-run` is already the safe preview).

`social_audit_calendar` spends **nothing** in any mode (it only reads assets): `dry-run` classifies + checks captions without downloading; `mock` does the full read-only inspection (downloads + measures) but writes nothing; `live` also writes each row's verdict to the **Audit Results** column. The audit only *reports* — it never regenerates, moves, deletes, or re-permissions an asset.

## Architecture

The **common thread** across the planned content types — generate AI media, push to a Drive folder, pull back for Cowork — lives in `content_hub/core/` and is **content-agnostic**. Each content type is its own package on top; `social/` is the only one so far.

```
server.py                thin FastMCP tools (one per social_* operation), stdio
content_hub/
  core/                  ← content-agnostic primitives, reused by every workflow
    config.py            env / Google creds / paths / brand + model defaults
    media.py             generate images (OpenAI gpt-image-2) + video (Google Veo); cost table
    drive.py             push to a Drive folder / pull latest / exists-check
    sheets.py            Sheets API read/write
    google_auth.py       OAuth "as you" (credentials.json → token.json)
    textcard.py          on-image text overlay for carousel slides
  social/                ← workflow #1 (blog/ and email/ would be siblings)
    rules.py             calendar naming, id→folder, Drive layout; plan_visual (aspect via specs)
    specs.py             canonical per-platform × per-post-type standards (aspect/resolution/
                         file-size/caption/hashtag caps); single source of truth for BOTH
                         generation (plan_visual) and the audit — dated + sourced
    calendar.py          read sheet → jobs; write link/cost back
    workflow.py          orchestrator: generate → push → writeback
    sheet_ops.py         create the living sheet shell
    edit_ops.py          in-place cell edits + bulk row appends (schema-aware guardrails)
    audit.py             compliance audit: measure real assets + captions vs specs; write
                         per-row verdicts to the Audit Results column (live)
    preview.py           the self-contained HTML review page
    exporters/           scheduler bulk-import files (registry: metricool, publer)
  cli.py                 manual dry/mock/live harness for the same operations
tests/                   property tests (pytest+hypothesis) for the pure specs/audit logic
```

**Adding a workflow** = a new package (e.g. `blog/`) that parses its own input format and defines its own Drive layout, reusing `core.media` + `core.drive` + `core.sheets`. **Adding a scheduler export** = a module in `exporters/` exposing `NAME`, `EXTENSION`, and `write(posts, out, **opts)`; it receives fully-resolved `Post` objects and never touches Drive or the sheet.

## Non-negotiable invariants

- **stdout is the MCP protocol channel.** The `core` engine must never write to stdout and never call `sys.exit`. All human-readable progress goes to **stderr** (tools pass an `emit` callback); tools return structured dicts.
- **Row ID is the stable key.** The Drive existence check matches on the `{RowID}_` prefix, so editing a headline never orphans an already-generated file. **Idempotency:** a row is skipped if its asset already exists on Drive — deleting the Drive file (or a carousel's group folder) is how you request a regeneration.
- **Schema-aware guardrails** (this is why `edit`/`add` are tools, not a raw Sheets connector): `Status` is never editable (approval is a human-only decision in the sheet); new rows default to `Draft`; the machine-owned columns (Generated Asset Link / Est. Cost / AI Model) are written only by `generate` and are `force`-gated in `edit`, refused in `add`; Platform / Format / Visual Type are validated against allowed values. A whole batch is validated first — if any edit is invalid, **nothing** is written.
- **Kind + aspect ratio derive from `Visual Type`**, not from `Format` alone (see the table in README.md). `Recorded video of Wiah` rows are never AI-generated.
- **Export URL form depends on the scheduler** — whether it *fetches* bytes or *parses* a Drive share link. Metricool fetches (needs `download` style); Publer resolves share links. See the Drive media-URL table in README.md before touching `exporters/`. Exported assets must stay link-shared; the exporter never changes Drive permissions.

## Config & secrets

`config.py` reads `.env` (minimal built-in loader, no dependency) and resolves `REPO_ROOT` as the folder holding the `content_hub` package. Images use **OpenAI `gpt-image-2`** (`OPENAI_API_KEY`), video uses **Google Veo** (`GEMINI_API_KEY`) — a run only needs the key(s) for what it generates. Google rotates Veo `-preview` model names; on a video model-not-found error, update `DEFAULT_VIDEO_MODEL` in `core/config.py`. **Never commit** `credentials.json`, `token.json`, or `.env`.
