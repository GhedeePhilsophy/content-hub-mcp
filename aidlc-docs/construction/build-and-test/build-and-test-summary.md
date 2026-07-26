# Build and Test Summary — Preview Platform Simulators

**Date**: 2026-07-26
**Unit**: `preview-simulators`

---

## Build Status

- **Build tool**: none — Python package run directly; the "build" is producing the HTML artifact
- **Build status**: **Success** (warm and cold)
- **Artifact**: `Ghedee_Social_Calendar_Q3_2026_preview.html` — 9,335,823 bytes, 120 posts
- **Build time**: seconds warm; **2m 26s cold** (`--no-cache`, 106 assets re-downloaded)
- **Verified**: `"source": "live"` in the result payload and `Review (live)` in the page title —
  both were wrong before this work due to a pre-existing variable-shadowing bug, now fixed

## Test Execution Summary

### Unit tests
- **Total**: 59 · **Passed**: 59 · **Failed**: 0
- **Coverage**: **not measured** — no coverage tooling is configured in this repo and none was
  added. Scope is deliberate: PBT is set to *Partial*, covering pure functions only.
- **Status**: **PASS**

### Integration tests
Two automated scenarios plus three manual ones (see
[integration-test-instructions.md](integration-test-instructions.md)).

| Scenario | Type | Status |
|---|---|---|
| 1. Page contract (Python emitter ↔ browser consumer) | Automated (Node) | **PASS** — 8/8 checks |
| 2. Structural regression on emitted HTML | Automated (Python) | **PASS** — 8/8 checks |
| 3. Simulator behaviour in a browser | Manual | Partly done — feed, profile, video posts exercised by the user; remaining sub-checks not run |
| 4. Video playback across three contexts | — | **RESOLVED — no longer applicable.** FR-8 superseded: inline playback removed after measurement (see below) |
| 5. Apps Script status-editing non-regression | Manual | **NOT RUN** — needs the deployed web app |

Automated detail — Scenario 1 also gates JS syntax (`node --check` on the page's script block)
and confirms all 120 rows have a card to clone from, ordering holds, engagement is coherent on
real data, and every icon the builders request exists.

- **Status**: **PASS for everything automatable; three manual scenarios outstanding.**

### Performance tests

| Measure | Target | Actual | Status |
|---|---|---|---|
| Artifact size vs baseline | ≤ +15% | **−10.2%** (10,399,175 → 9,335,823) | **PASS** |
| Cold build duration | no target | 2m 26s | Informational |
| Client-side responsiveness | no stall | **NOT MEASURED** — needs a browser | Outstanding |

The size result is the headline: the budget allowed growth, and the page shrank by ~1 MB.

### Additional tests
- **Contract tests**: covered by Scenario 1 (this is the only cross-boundary contract)
- **Security tests**: **N/A** — Security Baseline opted out at Requirements Analysis; no new
  I/O, no new dependency, no new attack surface. Note the feature does add two deliberate
  hardening details: page data is written with `createElement`/`textContent` rather than
  `innerHTML`, and `<` is escaped in the emitted JSON so a caption containing `</script>`
  cannot terminate the script block.
- **E2E tests**: **N/A** as automation; the manual scenarios above are the E2E coverage

---

## Overall Status

- **Build**: **Success**
- **Automated tests**: **PASS** (59 unit + 16 integration checks, 0 failures)
- **Ready for Operations**: **Not yet** — one open item below

## Resolved since first issue of this summary

**The inline-video item is closed by removal, not by verification.** Testing in a real browser
showed Drive blocks a native `<video>` off-site (`Origin: null` → `403`, no
`Access-Control-Allow-Origin`; no-cors path blocked by `Cross-Origin-Resource-Policy:
same-site`), while Drive's own `/preview` player works but draws a toolbar over the picture.
Offered the choice, the user elected to **drop inline playback**. FR-8 is superseded; video
posts show the poster frame with a play button opening the clip on Drive. Full measurements and
the two wrong diagnoses that preceded them are in
[../preview-simulators/code/code-summary.md](../preview-simulators/code/code-summary.md).

Net effect on risk: the largest open unknown in this feature is gone, and with it the embed
code. Page size dropped again to **9,326,379 bytes (−10.3% vs baseline)**.

## Next Steps

1. Finish manual Scenario 3 (the remaining simulator sub-checks) and Scenario 5 (Apps Script
   non-regression, needs the deployed web app).
2. Commit / PR / merge.
3. Publish the page to Drive (a run of `social preview Q3_2026` without `--no-publish`).
