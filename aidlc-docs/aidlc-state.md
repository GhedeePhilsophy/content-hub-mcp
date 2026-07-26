# AI-DLC State

**Project**: content-hub-mcp — Content Hub MCP server (Social Calendar workflow)
**Type**: Brownfield
**Feature (current)**: Preview platform simulators — true-to-life Instagram / Facebook / TikTok
feed simulators in the social-calendar preview page

## Current Phase
**COMPLETE** (2026-07-26). All stages executed or explicitly skipped with rationale. Feature
reviewed in-browser by the user and signed off.

**Outstanding, non-blocking**: Apps Script status-editing non-regression (Scenario 5) needs the
deployed web app; and the page has not yet been published to Drive — run
`python -m content_hub.cli social preview Q3_2026` (without `--no-publish`) when wanted.

## Execution Plan Summary
- **Stages to execute**: Functional Design, Code Generation, Build and Test
- **Stages skipped**: Reverse Engineering, User Stories, Application Design, Units Generation,
  NFR Requirements, NFR Design, Infrastructure Design (rationales in the execution plan)
- **Risk**: Low–Medium · **Rollback**: Easy (one file, one commit) · **Testing**: Moderate

## Stage Ledger — current feature (preview simulators)
| Phase | Stage | Status |
|-------|-------|--------|
| INCEPTION | Workspace Detection | ✅ Complete — brownfield, existing aidlc-docs, resumed |
| INCEPTION | Reverse Engineering | ⏭️ Skipped as standalone — targeted RE of the preview subsystem only (`social/preview.py`, `social/specs.py`); full RE artifacts not needed for a single-module change |
| INCEPTION | Requirements Analysis | ✅ Approved 2026-07-26 — `inception/requirements/preview-simulators/requirements.md` (11 FRs, 7 NFRs) |
| INCEPTION | User Stories | ⏭️ Skipped — internal tooling, single stakeholder, acceptance criteria already in requirements |
| INCEPTION | Workflow Planning | ✅ Complete — `inception/plans/execution-plan.md` awaiting approval |
| INCEPTION | Application Design | ⏭️ Skipped — no new component/service boundary; extends `preview.py` |
| INCEPTION | Units Generation | ⏭️ Skipped — single package, single file, UI-only; sequencing handled as phases inside the code-generation plan |
| CONSTRUCTION | Functional Design | ✅ Approved 2026-07-26 — `construction/preview-simulators/functional-design/simulator-design.md`. Defaults taken on both embedded questions: emerging-account engagement scale; carousels rebound |
| CONSTRUCTION | NFR Requirements | ⏭️ Skipped — NFRs already in requirements §4; no tech-stack selection |
| CONSTRUCTION | NFR Design | ⏭️ Skipped — NFR Requirements skipped; page-size design folded into Functional Design |
| CONSTRUCTION | Infrastructure Design | ⏭️ Skipped — no infrastructure; static HTML artifact |
| CONSTRUCTION | Code Generation | ✅ Approved 2026-07-26 — 46/47 plan items; the 47th (Drive video embed verification) needs a human browser. Post-approval tweak: status frames removed from IG Profile tiles |
| CONSTRUCTION | Build and Test | ✅ Approved 2026-07-26 — 5 instruction artifacts in `construction/build-and-test/`. 59 unit tests + 16 automated integration checks pass; warm + cold builds succeed; NFR-2 met with room to spare (−10.2% vs a +15% budget). 3 manual browser scenarios outstanding |
| OPERATIONS | Operations | ⏭️ Placeholder — N/A for a static HTML artifact; deployment/rollback notes recorded in `operations/operations.md` |

## Outcome
- **Delivered**: Simulator overlay with Instagram (Feed / Profile / Reels), Facebook and TikTok
  surfaces; phone chassis with strip-away toggle; newest-first ordering; status filter carried
  through and mirrored in the overlay; hover-revealed Row ID / date / status; deterministic
  simulated engagement counts. Standalone IG Grid removed and re-homed as the IG Profile tab.
- **NFR-2**: page **9,326,379 bytes vs a 10,399,175 baseline — 10.3% smaller**, against a budget
  that allowed +15% growth. Achieved by cloning media nodes instead of re-emitting them, and by
  deleting the IG Grid's duplicate 340px encodings.
- **FR-8 superseded**: inline video playback was built, measured and removed — Drive blocks
  off-site `<video>` (`Origin: null` → 403, no ACAO; no-cors blocked by CORP `same-site`) and its
  own `/preview` player draws a toolbar over the picture. Video posts show the poster frame with
  a play button opening the clip on Drive.
- **Tests**: 59 unit (14 new) + a Node page-contract check (`tests/page_contract.js`) covering
  the Python→browser seam that no unit test sees.

## Extension Configuration
| Extension | Enabled | Decided At |
|---|---|---|
| Security Baseline | **No** | Requirements Analysis (2026-07-26) |
| Resiliency Baseline | **No** | Requirements Analysis (2026-07-26) |
| Property-Based Testing | **Partial** | Requirements Analysis (2026-07-26) — pure functions only |

Full rule files for Security and Resiliency were **not** loaded (opted out). Property-Based
Testing is Partial: the simulator work is browser-side rendering, so PBT applies only to any
pure helper functions added on the Python side.

## Notes — current feature
- Target module: `content_hub/social/preview.py` (single self-contained HTML page, all assets
  inlined as data URIs — no external requests; the page is uploaded to Drive and also served
  through an Apps Script web app where status editing writes back to the living sheet).
- Existing preview already has: per-post platform chrome (IG card / FB post / TikTok 9:16 +
  action rail), week grouping, status filter chips, and an "IG Grid" profile view.
- Gap this feature fills: no full-feed, device-framed **simulator** view per platform that a
  reviewer can click into and scroll as the audience would see it.
- Constraint to respect: the page must stay a single portable file (self-contained, offline,
  shareable) and must not regress the Apps Script live-status-editing path.

---

## Completed features (archive)

### Social Calendar post audit — asset + caption compliance vs. per-platform standards
**Status**: ✅ **Complete and merged.** Base feature PR #16; Tier 1+2 follow-up (extra checks +
split Audit Status / Audit Note columns) PR #17 (`b350b18`). Live run against the living Q3_2026
sheet done and successful 2026-07-26 (reported by the user) — schema migration, PASS/WARN/FAIL
dropdown + green/yellow/red conditional formatting, and verdict write-back all applied.

| Phase | Stage | Status |
|-------|-------|--------|
| INCEPTION | Workspace Detection | ✅ Complete |
| INCEPTION | Reverse Engineering | ⏭️ Skipped as standalone — targeted RE of the audit-relevant subsystem instead |
| INCEPTION | Requirements Analysis | ✅ Approved 2026-07-25 |
| INCEPTION | User Stories | ⏭️ Skipped — internal tooling, single stakeholder |
| INCEPTION | Workflow Planning | ✅ Complete |
| INCEPTION | Application Design | ⏭️ Folded into taxonomy analysis + requirements §3.1 |
| INCEPTION | Units Generation | ✅ Two units (A: specs+generation, B: audit) |
| CONSTRUCTION | Functional Design (Unit A) | ✅ Captured in taxonomy analysis |
| CONSTRUCTION | Code Generation (Unit A) | ✅ specs.py + plan_visual refactor + tests |
| CONSTRUCTION | Code Generation (Unit B) | ✅ audit.py + MCP tool + CLI |
| CONSTRUCTION | Build and Test | ✅ 45 property tests pass; dry-run → mock → live all clean |

**Key decisions**
- 2026-07-25: Delivery = tool + sheet write-back; Inspection = download & inspect real assets;
  Captions = objective limits only; Standards = curated in-code specs table (dated + sourced).
- 2026-07-25: Stories out of scope.
- 2026-07-25: Taxonomy correction = shared source of truth + fix generation (canonical
  `specs.py`; `plan_visual` refactored to consume it).
