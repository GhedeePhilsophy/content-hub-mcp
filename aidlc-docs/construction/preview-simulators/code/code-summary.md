# Code Summary — Preview Platform Simulators

**Date**: 2026-07-26
**Plan**: [../../plans/preview-simulators-code-generation-plan.md](../../plans/preview-simulators-code-generation-plan.md)

---

## Files

| File | Change |
|---|---|
| `content_hub/social/preview.py` | **Modified** — the whole feature |
| `tests/test_preview_engagement.py` | **Created** — 14 tests for the pure logic |
| `README.md` | **Modified** — new "The review page (and the platform simulators)" section |
| `CLAUDE.md` | **Modified** — architecture-map line + a new non-negotiable invariant |

Nothing else was touched. `server.py`, `cli.py`, `calendar.py`, `rules.py`, `specs.py`,
`audit.py`, `workflow.py`, `edit_ops.py`, `sheet_ops.py`, `exporters/` and `core/*` are
unchanged — `build_preview()`'s signature was deliberately held constant.

### What changed inside `preview.py`

**Added (Python)**: `_fnv1a32`, `_band`, `engagement`, `_rel_time`, the `_ENGAGE_LIKES` /
`_ENGAGE_VIEWS` bands, six simulator icons in `SVG`, the `SIM_POSTS` metadata build, and the
`{{SIM_POSTS}}` / `{{ICONS}}` placeholders.
**Added (page)**: the `#sim` overlay markup, ~150 lines of simulator CSS, and the simulator
JS engine (state, routing, cloning, five surface builders, carousel rebinding, lazy video).
**Removed**: `_grid_cell()`, the `#grid` section and its chip, the `{{GRID}}` placeholder, and
the now-dead grid CSS.
**Changed**: `data-rowid` added to `.card`; `applyFilter()` lost its grid branch and gained a
call to `__simRefresh`.

---

## Measured results

### Page weight (NFR-2) — the headline number

```
baseline  10,399,175 bytes
new        9,336,845 bytes
delta     -1,062,330 bytes  (-10.2%)
```

**NFR-2 budget was "under +15% growth". The page got 10.2% smaller.**

The design predicted this and the build confirmed it. Two effects net out: the simulators add
only markup, CSS, JS and a ~120-entry JSON metadata block (no image bytes, because every
surface clones media nodes the review feed already holds), while deleting the standalone IG
Grid removed a second, 340px encoding of every Instagram asset. The thumbnail cache sheds those
entries too — `_ImgCache.save()` prunes anything not requested during a run.

### Tests

`pytest tests/` — **59 passed** (45 pre-existing, 14 new). Includes a cross-process determinism
test that spawns a fresh interpreter: that is the test that would catch a regression to
`hash()`, which is salted per process and would make every rebuild change the counts.

### Structural checks on the emitted page (120 posts, live Q3_2026)

| Check | Result |
|---|---|
| Simulator overlay + chip present | PASS |
| IG Grid chip and `#grid` section gone | PASS |
| No unreplaced `{{PLACEHOLDER}}` | PASS |
| `data-rowid` on all 120 `<article class="card">` | PASS |
| Apps Script handlers intact (`setPostStatus`, `getPostStatuses`, `getViewerInfo`) | PASS |
| No external `<img>`/`<script>`/`<iframe>`/`<link>` — NFR-1 self-contained | PASS |
| `SIM_POSTS` = 120 entries, all three platforms | PASS |
| Ordering newest-first (2026-11-19 → 2026-08-31), undated last | PASS |
| Outbound `<a>` hosts limited to docs.google.com / drive.google.com (pre-existing) | PASS |

---

## Deviations from the plan

1. **`_grid_cell()` deleted rather than kept (Step 3).** The plan said keep it for the Profile
   tab; the design said the Profile tab clones the review card's `<img>`. Those are mutually
   exclusive — keeping it would have kept emitting the duplicate 340px encodings the design set
   out to remove, while leaving the function unreferenced. Deleting it is what produces the
   -10.2%. Its corner-badge and status-frame logic is reimplemented in the Profile builder.
2. **Surface memoization dropped (Step 6).** Only the visible surface is ever built, which is
   what the plan's laziness was for (no stall constructing five surfaces at once). The
   `built[key]` cache on top would have needed invalidation on every status-filter change for a
   rebuild that measures as instant; it and the unused `surfaceKey()` were removed rather than
   left as dead code.

## Incidental fix (pre-existing bug, not introduced here)

`build_preview`'s week loop used `key, label = _week_of(...)`, clobbering the `label` returned
by `fetch_calendar`. The page title and the returned `source` field therefore reported a week
heading instead of the source. Confirmed present in `HEAD` before this work (`git show
HEAD:…preview.py` line 583). Renamed to `wk_key, wk_label`; the build now correctly reports
`"source": "live"` and titles the page `Review (live)`.

---

## Inline video: built, measured, and removed (FR-8 superseded)

**Outcome: there is no inline playback.** Video posts show the poster frame with a play button
that opens the clip on Drive — the pre-existing behaviour. This took three rounds to reach, and
the wrong turns are worth recording so nobody retries them.

### What was tried

| Attempt | Result |
|---|---|
| Drive `/file/d/<id>/preview` iframe | **Works** — confirmed playing in a local browser. But it is a player *page* and draws its own toolbar above the picture: the "black band" the user reported. |
| Aspect-ratio adoption from the poster's natural size | **Wrong diagnosis.** I assumed the band was letterboxing from a Format/asset shape mismatch. It was Google's chrome — the pop-out icon in the user's second screenshot proved it. |
| Native `<video>` on `drive.usercontent.google.com/download` | **Blocked.** The URL serves `video/mp4`, 13.4 MB, valid MP4, HTTP 200 — *to an anonymous request*. A browser sends `Origin: null` from a `file://` page and Drive returns **403 with no `Access-Control-Allow-Origin`**. |
| `crossOrigin='anonymous'` to bypass CORP | **Also blocked.** My probe saw `Access-Control-Allow-Origin: *` only because urllib sent no `Origin` header. With a real `Origin: null`, Drive 403s. Both the CORS and no-cors doors are shut (`Cross-Origin-Resource-Policy: same-site`). |

### Method note — how the wrong turns happened

The first two attempts were **guesses dressed as fixes**: I inferred the cause from a screenshot
instead of measuring. The third attempt was measured, but I measured with the wrong client —
`urllib` without an `Origin` header does not reproduce what a browser sends, and that single
difference inverted the result. The console log the user supplied is what finally settled it.
**Reproduce the client's actual request, or the measurement is worthless.**

### Decision

Presented with three options — crop Google's toolbar (clean but loses playback controls), keep
the toolbar (functional but unrealistic), or drop inline playback — the user chose **drop**.
A paused feed shows stills anyway, so the realism cost is small and the failure surface is now
zero.

The aspect-adoption code was removed along with the rest; the underlying issue it addressed (a
Reel row whose clip is landscape) is real and is already flagged by `social_audit_calendar` as
an aspect FAIL. That is a regeneration decision, not a preview one.

Risk if it fails: **low, and contained.** A blocked embed degrades silently to today's
behaviour — poster frame plus a play button that opens the clip on Drive — with a 6-second
timeout removing the iframe. Offline viewers never even attempt it (`navigator.onLine` guard).
So the failure mode is "video doesn't play inline in that one context", not a broken page.
