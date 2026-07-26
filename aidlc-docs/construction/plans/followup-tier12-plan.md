# Follow-up increment — Tier 1 + Tier 2 checks & split Audit columns

**Base**: the merged post-audit feature (PR #16). **Depth**: requirements delta + build.

## Requirements delta

### New checks
**Tier 1**
- **T1a Video duration** — measure clip length (imageio metadata) and compare to the post
  type's min/max (Reel/video). Over the hard max → FAIL; under the recommended min → WARN.
  Add `min_seconds` / `max_seconds` to `specs.MediaSpec`.
- **T1b Row readiness / status coherence** (no download) — an **Approved** row with no asset
  (and not a recorded-awaiting clip) or a `Failed` asset → FAIL; empty caption on an Approved
  row → WARN; missing Platform / Format / Date / Time → WARN.
- **T1c Asset link-shared** — the row's Drive asset (or carousel folder) must be shared
  "anyone with link" or the scheduler fetch fails silently → WARN when not shared.

**Tier 2**
- **T2a Links in caption on IG/TikTok** — a URL in the caption isn't clickable there → WARN.
- **T2b Hashtags in the IG caption body** — IG best practice is first-comment hashtags → WARN.
- **T2c Carousel Slides vs folder** — `Slides` column disagrees with the actual image count
  → WARN; and **intra-carousel aspect consistency** (all slides share slide 1's ratio) → WARN.
- **T2d Duplicate hashtags** (within a post) and **duplicate assets** (same Drive md5 reused
  across rows) → WARN.

### Output change — split `Audit Results` into two columns
- **`Audit Status`** — one of `PASS` / `WARN` / `FAIL` (a data-validation dropdown of those
  three values), with a **background fill matching the verdict**: PASS green, WARN yellow,
  FAIL red (via Sheets conditional formatting, same mechanism as the human Status column).
- **`Audit Note`** — the messages for a non-PASS verdict; **blank when PASS**.
- Row verdict is **worst-wins**: any FAIL ⇒ FAIL (even with WARNs present); else any WARN ⇒
  WARN; else PASS. A fully-NA row leaves Status blank.

### Migration
- Shell (new calendars): `SHELL_HEADERS` replaces `Audit Results` with `Audit Status` +
  `Audit Note`; the shell installs the dropdown + conditional formatting.
- Living sheet (live mode): the audit ensures both columns exist — rename an existing
  `Audit Results` → `Audit Status` and append `Audit Note`, else append both — then installs
  the dropdown + colour rules once (idempotent: skip if the column already has colour rules).

## Build checklist
- [x] `specs.py`: `min_seconds`/`max_seconds` on MediaSpec; Reel/video cells filled
      (IG/FB reel 3–90s, FB video ≤240min, TikTok 3–600s).
- [x] `drive.py`: `is_link_shared(file_id)` (any `type=anyone` reader; fails open).
- [x] `core/sheets.py`: `sheet_meta()` + `apply_requests()` (formatting batchUpdate).
- [x] `audit.py`: new checks (duration, readiness, link-shared, caption links, IG hashtag
      placement, carousel slides/consistency, duplicate hashtags + md5 post-pass); two-column
      write-back (`_ensure_audit_columns`) + colour/validation install.
- [x] `calendar.py`: split shell columns + `audit_status`/`audit_note` aliases + dropdown +
      `add_audit_conditional_formatting`; `AUDIT_STATUS_VALUES` + `AUDIT_STATUS_FILLS`.
- [x] `tests/`: 45 total pass (added find_urls, duplicate_hashtags, caption links/placement,
      readiness, duration bounds, worst-wins FAIL>WARN, status_word/note_text).
- [~] Validate: dry-run clean (55 pass / 65 warn — mostly real duplicate/repeated hashtags,
      verified); mock in progress; live (schema migration + coloured write-back) pending go.
