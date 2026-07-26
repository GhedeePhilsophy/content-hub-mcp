# Code Generation Plan — Preview Platform Simulators

**This plan is the single source of truth for the Code Generation stage.** Steps execute in
order; each is marked `[x]` immediately on completion, in the same interaction as the work.

**Design**: [../preview-simulators/functional-design/simulator-design.md](../preview-simulators/functional-design/simulator-design.md)
**Requirements**: [../../inception/requirements/preview-simulators/requirements.md](../../inception/requirements/preview-simulators/requirements.md)

---

## Unit context

- **Unit**: `preview-simulators` (single unit — Units Generation was skipped; this is one
  package, one primary file).
- **Project type**: Brownfield. **All edits modify existing files in place.** No `_new` /
  `_modified` copies.
- **Workspace root**: `c:\Kobie\Projects\Wiah\Ghedee Philosophy Website\content-hub-mcp`
- **Primary target**: `content_hub/social/preview.py`
- **Dependencies (read-only, unchanged)**: `social/calendar.py` (`Job` rows),
  `social/rules.py`, `core/drive.py` (`file_id_from_link`), `core/config.py`.
- **Interfaces held constant**: `build_preview(calendar_id, version, *, out_path, no_cache,
  publish, emit)` — signature unchanged, so `server.py` and `cli.py` need no edit.
- **Decisions carried in** (defaults taken; the user did not override):
  - Engagement scale = **emerging account** (tens–low hundreds of likes).
  - Carousels in the simulator = **rebind** the existing swipe controls.

---

## Step 1 — Pure logic: deterministic engagement
**Traces**: FR-10
**File**: `content_hub/social/preview.py` (modify)

- [x] Add `_fnv1a32(s: str) -> int` — FNV-1a 32-bit, no dependency, stable across processes
      (explicitly not `hash()`, which is per-process salted).
- [x] Add `engagement(row_id, platform, is_reel) -> dict` — pure, total, deterministic.
      Draws are salted per metric (`_band`) rather than sliced from one hash, which is simpler
      and gives unlimited independent draws; secondary metrics derived from the primary draw so
      `comments <= likes <= views`. Emerging-account bands.
- [x] Add `_rel_time(date_str, newest_date) -> str` — "2h" / "3d" / "1w"; empty for undated rows.
- [x] Bands defined as module-level constants (`_ENGAGE_LIKES`, `_ENGAGE_VIEWS`) so the scale is
      a one-line change later.

## Step 2 — Row identity + simulator metadata
**Traces**: FR-1, FR-4, FR-5, FR-6, FR-9, FR-10
**File**: `content_hub/social/preview.py` (modify)

- [x] Add `data-rowid` to the `.card` article in `_card()` (the only change to existing card
      markup; `.gcell` already has one).
- [x] Build a `SIM_POSTS` list in `build_preview()` — one entry per row: `row_id, platform,
      date, day, status, fmt, is_reel, is_carousel, handle, caption, hashtags, hook,
      asset_link, kind, video_id, engagement, rel_time`. **Metadata only — no image bytes.**
- [x] Sort per D3: date desc → undated last → row_id desc.
- [x] Emit as JSON into the page via a new `{{SIM_POSTS}}` placeholder, with `<` escaped so a
      caption containing `</script>` cannot terminate the script block. Also added an
      `{{ICONS}}` placeholder so the JS reuses the existing Python `SVG` table rather than
      restating every icon.

## Step 3 — Remove the standalone IG Grid
**Traces**: FR-3
**File**: `content_hub/social/preview.py` (modify)

- [x] Delete the `grid` chip from the chips row (the `chip-sep` is kept — it now separates the
      filter chips from the new Simulator chip).
- [x] Delete the `#grid` section assembly and the `{{GRID}}` placeholder from `_PAGE`.
- [x] Remove the `flt.f === 'grid'` branch from `applyFilter()` — done, and `applyFilter` now
      also calls `window.__simRefresh()` so one filter change drives both views.
- [x] ~~**Keep `_grid_cell()`**~~ — **DEVIATION FROM PLAN: `_grid_cell()` was deleted instead.**
      The plan said to keep it for the Profile tab, but D1 specifies that the Profile tab clones
      the review card's `<img>` and scales it with CSS. Those two are mutually exclusive:
      keeping `_grid_cell` would have kept emitting a second 340px encoding of every Instagram
      asset — exactly the duplicated weight D1 set out to remove — while leaving the function
      itself unreferenced dead code. Deleting it is what delivers the NFR-2 win. The corner
      badge / status-frame logic it carried is reimplemented in the Profile builder (Step 7).
- [x] `#grid .profile` / `.iggrid` CSS retained and re-scoped — the Profile tab reuses the same
      visual treatment.

## Step 4 — Simulator shell: markup + CSS
**Traces**: FR-1, FR-2, FR-7, ASSUMPTION-1
**File**: `content_hub/social/preview.py` → `_PAGE` (modify)

- [x] Add the `Simulator` chip to the chips row (`data-testid="sim-open-chip"`).
- [x] Add the `#sim` overlay root: `.sim-top` (close ×, platform tabs with counts, status-filter
      mirror, chassis toggle) + `.sim-stage` > `.phone` > `.phone-status` + `.app`.
- [x] CSS: overlay + backdrop, phone chassis (body, rounded corners, notch, simulated status
      bar), `[data-chassis="off"]` strip-away variant, theme-aware tokens, responsive rules so
      the overlay never scrolls the page body horizontally (NFR-6).
- [x] `data-testid` on every interactive control per the automation rules:
      `sim-close`, `sim-tab-instagram|facebook|tiktok`, `sim-chassis-toggle`,
      `sim-status-<kind>`, `sim-igtab-feed|profile|reels`.

## Step 5 — Surface CSS (five surfaces)
**Traces**: FR-6
**File**: `content_hub/social/preview.py` → `_PAGE` (modify)

- [x] Instagram Feed — header, media, action row, engagement line, caption with the `…more`
      fold at 125 chars, first-comment hashtags.
- [x] Instagram Profile — profile header + 3-column grid. **Correction to the plan's wording**:
      it does *not* reuse `_grid_cell` output (that function was deleted in Step 3); the tiles are
      built in JS from cloned `<img>` nodes, with the corner badge and status frame
      reimplemented as `.s-gcorner` / `.s-gcell::after`.
- [x] Instagram Reels — snap-scroll 9:16 surface.
- [x] Facebook — caption above media, reaction bar, counts.
- [x] TikTok — full-bleed 9:16 snap-scroll, right action rail, bottom caption block.
- [x] Per-platform app chrome: top bars and bottom nav bars.
- [x] `.sim-meta` hover overlay — absolutely positioned, no layout shift (FR-9).

## Step 6 — Simulator JS: state, routing, cloning
**Traces**: FR-1, FR-2, FR-4, FR-5, FR-6, FR-7
**File**: `content_hub/social/preview.py` → `_PAGE` (modify)

- [x] `simState = {open, platform, igTab, chassis, built}`; status filter deliberately **not**
      duplicated — reads the existing `flt.s`.
- [x] Open/close: chip → open; ×, `Esc`, backdrop → close. Lock/restore body scroll. Never
      unmount the review feed (it is the clone source).
- [x] `simPosts(platform, extra)` — membership + ordering + status filter per D3.
- [x] `cloneMedia(rowId)` — `document.querySelector('.card[data-rowid="…"] .media').cloneNode(true)`;
      returns null when a row has no media node.
- [x] **DEVIATION FROM PLAN — memoization dropped.** Only the *visible* surface is ever built
      (`render()` runs on open and on each tab switch), which is what the plan's laziness was for:
      the simulator never stalls constructing all five surfaces at once. The `built[key]` cache on
      top of that was not implemented — it would have needed invalidation on every status-filter
      change for a rebuild that measures as instant, and a half-used `built` map plus an unused
      `surfaceKey()` would have been dead code. Both were removed rather than left in place.
- [x] Rebind the existing carousel binder over cloned `.media.carousel` nodes (decision: rebind).
- [x] Tab counts update with the status filter; changing the filter invalidates `built` and
      rebuilds the visible surface.
- [x] Chassis toggle = data-attribute flip, no rebuild.

## Step 7 — Surface builders
**Traces**: FR-6, FR-9, FR-10
**File**: `content_hub/social/preview.py` → `_PAGE` (modify)

- [x] `buildIGFeed`, `buildIGProfile`, `buildIGReels`, `buildFB`, `buildTikTok`.
- [x] Each composes: cloned media + platform chrome + engagement line + caption + `.sim-meta`
      hover block (Row ID · date · status dot).
- [x] Empty-surface panel: "No posts match the current filter" inside the phone, not a blank
      device.
- [x] All DOM built with `createElement`/`textContent` (no `innerHTML` with row data), so
      captions containing markup can't break the page.

## Step 8 — Inline video with fallback
**Traces**: FR-8, ASSUMPTION-2 / D5
**File**: `content_hub/social/preview.py` → `_PAGE` (modify)

- [x] Poster + play button by default; on click, swap in
      `https://drive.google.com/file/d/<id>/preview` in an iframe. **Lazy — never pre-mount.**
- [x] Fallback when `navigator.onLine` is false or the iframe does not load within a short
      timeout: revert to poster + a link opening the clip on Drive in a new tab. Silent
      degradation; no broken frame.
- [ ] **NOT DONE — cannot be verified from this environment.** Verifying the Drive iframe
      player requires loading the page in a real browser, signed in to Google, in each of the
      three contexts (local `file://`, the Drive-hosted copy, the Apps Script web app). There is
      no browser or authenticated session available to me here, so this is the one item I cannot
      close; **the user must click play on a video post in each context.** The code is written
      so that a blocked embed degrades silently to today's poster + Drive link, which is why
      this being unverified is a fidelity question, not a breakage risk. Tracked in the code
      summary as the single outstanding item.

## Step 9 — Tests
**Traces**: FR-10; Property-Based Testing = Partial (pure functions only)
**File**: `tests/test_preview_engagement.py` (create)

- [x] Determinism — same row_id ⇒ identical output across calls.
- [x] Coherence — `comments <= likes`, `likes <= views` where views exist; all non-negative ints.
- [x] In-band — every value inside its declared band per platform/post type.
- [x] Totality — never raises for any string, including empty and non-ASCII (hypothesis).
- [x] `_rel_time` monotonicity — older dates never produce a "newer" label.

## Step 10 — Documentation
**Traces**: deliverables list in the execution plan
**Files**: `README.md`, `CLAUDE.md` (modify)

- [x] README: describe the simulator overlay, the three platforms + IG sub-tabs, the chassis
      toggle, the video-streaming behaviour and its fallback, and the removal of the IG Grid.
- [x] CLAUDE.md: update the `preview.py` line in the architecture map.

## Step 11 — Code summary artifact
**File**: `aidlc-docs/construction/preview-simulators/code/code-summary.md` (create)

- [x] Record files modified vs created, the measured output-size delta, and the video-embed
      verification result per context.

---

## Traceability — requirement → step

| Requirement | Step(s) |
|---|---|
| FR-1 Entry point / close behaviour | 4, 6 |
| FR-2 Platform tabs + counts | 4, 6 |
| FR-3 IG Grid removed, re-homed | 3, 5, 7 |
| FR-4 Newest-first ordering | 2, 6 |
| FR-5 Status filter carries through | 2, 6 |
| FR-6 Per-platform surfaces | 5, 7 |
| FR-7 Chassis + toggle | 4, 6 |
| FR-8 Video streaming + fallback | 8 |
| FR-9 Hover metadata | 5, 7 |
| FR-10 Deterministic engagement | 1, 2, 7, 9 |
| FR-11 No regression | 3, 6 (verified in Build & Test) |
| NFR-1 Single self-contained file | 6 (cloning — no new external refs) |
| NFR-2 Page weight | 1–3, 6 (measured in Build & Test) |
| NFR-3/4/5 stdout / no spend / no dependency | all — no new I/O or imports |
| NFR-6 Theme + responsive | 4, 5 |
| NFR-7 Accessibility | 4, 6 |

---

## Scope

**11 steps.** One primary file modified (`content_hub/social/preview.py`), one test file created,
two docs modified, one summary artifact created. No new Python dependency, no new I/O, no change
to the MCP tool or CLI signatures.

**Not touched**: `server.py`, `cli.py`, `calendar.py`, `rules.py`, `specs.py`, `audit.py`,
`workflow.py`, `edit_ops.py`, `sheet_ops.py`, `exporters/`, `core/*`.
