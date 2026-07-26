# Integration Test Instructions

## Purpose

The unit tests cover pure Python. The real integration risk in this feature sits at two seams
neither pytest nor the type system can see:

1. **Python emitter → browser consumer** — `build_preview()` writes `SIM_POSTS` / `ICONS` into
   the page; the simulator JS reads them. A field rename or a bad escape breaks the simulator
   silently, and the Python side still exits 0.
2. **Simulator → review feed** — the simulator clones media nodes *out of the review feed's
   DOM*. If a card stops carrying `data-rowid`, or its media class changes, posts render with
   no image and nothing errors.

Both are covered below: the first automatically, the second automatically plus by eye.

---

## Scenario 1 — Page contract (automated)

- **Description**: parse the built page as a browser would and assert the emitter/consumer
  contract holds.
- **Requires**: Node.js.
- **Setup**: build a page first (see build-instructions.md).

```powershell
# 1. build the artifact
python -m content_hub.cli social preview Q3_2026 --no-publish

# 2. extract the page's script block
python -c "import re,pathlib; h=pathlib.Path('Ghedee_Social_Calendar_Q3_2026_preview.html').read_text(encoding='utf-8'); pathlib.Path('page.js').write_text('\n'.join(re.findall(r'<script>(.*?)</script>', h, re.S)), encoding='utf-8')"

# 3. syntax-gate the JS, then run the contract checks
node --check page.js
node tests/page_contract.js Ghedee_Social_Calendar_Q3_2026_preview.html page.js
```

- **Expected results** (all must PASS; the script exits non-zero on any failure):
  - `SIM_POSTS` and `ICONS` parse as JavaScript
  - every icon the builders request exists in `ICONS` (9 distinct) — a missing one renders a
    blank control
  - every `SIM_POSTS` row has a matching `<article class="card" data-rowid=…>` to clone from
  - dated rows are newest-first; undated rows sort last (FR-4)
  - engagement is coherent on every real row (FR-10)
  - only the three known platforms appear
- **Cleanup**: `rm page.js`

---

## Scenario 2 — Structural regression (automated)

- **Description**: confirm the feature's removals and non-regressions in the emitted HTML.
- **Test steps**: run against a freshly built page:

```powershell
python - <<'PY'
import re, pathlib
h = pathlib.Path('Ghedee_Social_Calendar_Q3_2026_preview.html').read_text(encoding='utf-8')
checks = {
 'simulator overlay present':   'id="sim"' in h,
 'Simulator chip present':      'data-testid="sim-open-chip"' in h,
 'IG Grid chip gone':           'data-f="grid"' not in h and 'IG Grid' not in h,
 '#grid section gone':          'id="grid"' not in h,
 'no unreplaced placeholders':  not re.search(r'\{\{[A-Z_]+\}\}', h),
 'data-rowid on every card':    len(re.findall(r'<article class="card ', h)) ==
                                len(re.findall(r'<article class="card [^>]*data-rowid=', h)),
 'apps script handlers intact': all(k in h for k in ('setPostStatus','getPostStatuses','getViewerInfo')),
 'self-contained (NFR-1)':      not re.findall(r'<(?:img|script|iframe)[^>]*src="http', h)
                                and not re.findall(r'<link[^>]*href="http', h),
}
for k, v in checks.items(): print(('PASS ' if v else 'FAIL ') + k)
raise SystemExit(0 if all(checks.values()) else 1)
PY
```

- **Expected results**: all PASS.
- **Note**: outbound `<a href>` links to `docs.google.com` / `drive.google.com` are expected —
  those are the pre-existing "Sheet" and "Asset" buttons, not embedded assets.

---

## Scenario 3 — Simulator behaviour (manual, in a browser)

No JS test runner exists in this repo, so these are checked by hand. Open the built page and
click **Simulator**.

| # | Check | Expected | Traces |
|---|---|---|---|
| 1 | Click the Simulator chip | Overlay opens on Instagram Feed, phone chassis on | FR-1, FR-2 |
| 2 | Switch Instagram / Facebook / TikTok | Each renders its own platform's posts; tab counts match | FR-2, FR-6 |
| 3 | Instagram sub-tabs Feed / Profile / Reels | All three render; Reels shows only Format=Reel rows | FR-6 |
| 4 | Scroll a feed | Newest post first | FR-4 |
| 5 | Change **Showing** to Approved | Simulator and tab counts update; the page's status chips underneath change too | FR-5 |
| 6 | Hover a post (and a Profile tile) | Row ID · date · status chip appears, no layout shift | FR-9 |
| 7 | Click **Phone frame** | Chassis strips away, feed widens; platform tab switch keeps the setting | FR-7 |
| 8 | Open a carousel post | Arrows, dots and the `1/N` badge work | design D6 |
| 9 | Press **Esc**, the ×, and the backdrop | Each closes; review feed keeps scroll position and filters | FR-1 |
| 10 | Set a filter that matches nothing | "No posts match the current filter" inside the phone, not a blank device | FR-6 |
| 11 | Press play on a video post | Opens the clip on Drive in a new tab; no embed, no black band | FR-8 |

---

## Scenario 4 — Video posts (**resolved — no inline playback**)

FR-8 was superseded on 2026-07-26: **there is no inline playback**, so there is nothing to test
across contexts. Video posts show the poster frame with a play button that opens the clip on
Drive.

| Check | Expected |
|---|---|
| A video post in any surface | Poster frame, no black band, no embed |
| Click its play button | The clip opens on Drive in a new tab |

**Do not re-attempt an inline embed** without reading the measurements in
[../preview-simulators/code/code-summary.md](../preview-simulators/code/code-summary.md).
Summary: Drive answers a browser's `Origin: null` with `403` and no `Access-Control-Allow-Origin`,
and blocks the no-cors path with `Cross-Origin-Resource-Policy: same-site`; Drive's own
`/preview` player works but draws a toolbar over the picture. Note that probing those URLs with
a script that sends **no `Origin` header** returns a misleading success — that mistake cost two
rounds.

---

## Scenario 5 — Apps Script non-regression (manual)

The simulator shares the page with the live status-editing path, so confirm it still works:

1. Open the page **through the Apps Script web app** (not the local file).
2. Confirm the "Signed in as …" badge appears.
3. Change a status pill in the review feed → toast confirms, and the sheet updates.
4. Open the Simulator, close it, then change a status again → still saves.

**Expected**: unchanged behaviour. The simulator adds no handlers to these controls and never
unmounts the review feed.
