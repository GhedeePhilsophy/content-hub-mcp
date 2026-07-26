# AI-DLC Audit Log

Append-only. Captures every user input (raw) and AI action with ISO-8601 timestamps.

---

## Workspace Detection
**Timestamp**: 2026-07-25T00:00:00Z
**User Input**: "Initialize AI-DLC session. Scan CLAUDE.md and the ./aws-aidlc-rules directory, then state the current phase."
**AI Response**: Loaded core-workflow.md, common/process-overview.md, common/session-continuity.md. Detected brownfield project (existing content_hub package, server.py, CLI) with no prior aidlc-state.md and no reverse-engineering artifacts. Reported current phase: INCEPTION / Workspace Detection complete; next applicable stage = Reverse Engineering, then Requirements Analysis. No files written pending a change request.
**Context**: INCEPTION — Workspace Detection

---

## Requirements Analysis — initial request
**Timestamp**: 2026-07-25T00:05:00Z
**User Input**: "Goal: Add functionality to audit the social calendar posts to ensure the that the assets and captions are appropriate for each type of post. We should look at characteristics like: 1. Image or video aspect ratio 2. Image or video resolution 3. Image or video file size 4. Size or content of post captions. Each characteristic should be evaluated against the: 1. Platform - Instagram, Facebook, or TikTok 2. Type of post - Reel, Video Post, Image post. The audit should be based on the most up-to-date standards and practices for each of the 3 platforms."
**AI Response**: Performed targeted reverse engineering of the relevant subsystem (rules.py, calendar.py, edit_ops.py, media.py, drive.py, exporters, preview.py, config.py, server.py). Confirmed feasibility with existing deps (Pillow, imageio/ffmpeg, Drive size metadata). Proceeding to Requirements Analysis clarifying questions.
**Context**: INCEPTION — Requirements Analysis (intent captured)

---

## Requirements Analysis — clarifying answers
**Timestamp**: 2026-07-25T00:15:00Z
**User Input**: (via structured questions) Delivery = "Tool + sheet write-back"; Inspection = "Download & inspect real assets"; Captions = "Objective limits only"; Standards = "Curated in-code spec table (dated + sourced)".
**AI Response**: Recorded the four decisions. Drafting requirements.md at Standard depth reflecting them; will present extension opt-ins (security / resiliency / property-based testing) at the requirements approval gate.
**Context**: INCEPTION — Requirements Analysis

---

## Construction (pre-work) — post-type taxonomy coverage analysis
**Timestamp**: 2026-07-25T00:30:00Z
**User Input**: "As part of construction, determine whether the current taxonomy covers all possible post types on the 3 social platforms."
**AI Response**: Web-verified (July 2026) the full post-type universe for Instagram/Facebook/TikTok. Verdict: the 4 buckets (Reel/Video Post/Image Post/Carousel) match the calendar's Format enum but are not self-sufficient — specs must key on platform×type; several platform×type combos are invalid/normalized (IG feed video = Reel; TikTok single image = Photo Mode; TikTok/IG video-post = 9:16 not 16:9; per-platform carousel caps IG 20 / FB 10 / TikTok 35). Real types outside taxonomy: Stories (all 3), TikTok Photo Mode as distinct carousel, FB text/link-only (no asset). Wrote aidlc-docs/construction/audit/functional-design/post-type-taxonomy-analysis.md with a validity matrix + recommendations. Stories recommended out of scope (not in Format enum); the rest in scope for the SPECS[platform][type] table.
**Context**: CONSTRUCTION — Functional Design input (taxonomy)

---

## Taxonomy correction — scope decision
**Timestamp**: 2026-07-25T00:40:00Z
**User Input**: "Stories are out of scope." + (via structured question) taxonomy correction scope = "Shared source of truth + fix generation".
**AI Response**: Confirmed Stories out of scope (documented as known limitation). Verified `plan_visual` has a single caller (calendar.py:348) and `VisualPlan` shape is unchanged, so the refactor is contained. Updated taxonomy analysis (Decision + blast-radius section), requirements.md (added canonical `specs.py` + `plan_visual` refactor to scope; FR-1a/1b/1c, FR-2a/2b), and aidlc-state.md (split into Unit A: specs+generation, Unit B: audit; A before B). Facebook 16:9 preserved; existing IG/TikTok video assets not auto-regenerated (surface as audit findings).
**Context**: CONSTRUCTION — Functional Design input (taxonomy) / scope expansion

---

## Requirements approval + extensions + workflow plan
**Timestamp**: 2026-07-25T00:55:00Z
**User Input**: "#1 Approved. #2 Explain this to me" → (after explanation) "go with your picks".
**AI Response**: Requirements approved. Extensions set: Security No, Resiliency No, PBT Partial (pure functions only; introduces pytest+hypothesis+tests/). Recorded extension config + stage ledger in aidlc-state.md. Wrote construction/plans/code-generation-plan.md: stage decisions (skip User Stories; fold App Design; two units A→B), mermaid, and per-unit checklists. Awaiting go-ahead to build Unit A.
**Context**: INCEPTION — Workflow Planning
**Note**: PBT extension enabled (Partial) — enforce for pure functions in specs/aspect/caption logic.

---

## Sheet-reflection decision
**Timestamp**: 2026-07-25T01:05:00Z
**User Input**: "Will the taxonomy update be reflected in the spreadsheet?" → (after options presented) "stay with a".
**AI Response**: Clarified the taxonomy update is code-side only (post type is derived from Platform+Format+Visual Type; no sheet columns/dropdowns change). Confirmed Option A (flag-only): the audit reports invalid platform×format combos as findings; no in-sheet legend (B) and no Apps Script conditional validation (C). Write-back target confirmed = `Revision (Claude)` column. All open design questions closed; ready to build Unit A on go.
**Context**: INCEPTION — Workflow Planning (final design confirmations)

---

## Code Generation — Unit A (specs + generation correction)
**Timestamp**: 2026-07-25T01:30:00Z
**User Input**: "go"
**AI Response**: Built Unit A. Web-verified 2026 spec numbers (noted IG image cap is 8 MB not 30, TikTok caption 4000). Added content_hub/social/specs.py (SPECS + CAPTIONS + target_aspect/media_spec/caption_spec/classify, sourced+dated). Refactored rules.plan_visual to take optional platform and source aspect from specs (VisualPlan shape unchanged). Updated the single call site calendar.py:348. Added requirements-dev.txt (pytest+hypothesis) and tests/test_specs.py (19 tests, all pass). Validated: import OK (no circular import), plan_visual regression matrix correct, generate --mode dry-run on live Q3_2026 planned 120 rows with no error. Only generation behaviour change vs legacy: IG & TikTok non-Reel video 16:9 → 9:16 (FB 16:9 kept; images/reels/carousels unchanged). Presented Unit A for review gate.
**Context**: CONSTRUCTION — Code Generation (Unit A)

---

## Code Generation — Unit B (the audit) + Build/Test
**Timestamp**: 2026-07-25T02:10:00Z
**User Input**: "Continue to Unit B"
**AI Response**: Built content_hub/social/audit.py (classify → resolve asset → download & measure aspect/resolution/file-size via Pillow+imageio → caption checks → PASS/WARN/FAIL verdicts → summary; carousel per-slide rollup; NA paths for failed/recorded/no-link). Added `revision` alias to calendar reader for write-back to Revision (Claude). Wired social_audit_calendar MCP tool (server.py) + `social audit` CLI subcommand. Added tests/test_audit.py (14 tests). Full suite 33 pass. Validated on live Q3_2026: dry-run (120 rows classified, captions checked) and mock (120 assets downloaded + measured) both exit 0. Results: 107 pass / 6 warn / 7 fail — the audit correctly flags the pre-existing IG 16:9 video assets (blast-radius items) as aspect FAIL vs 9:16, surfaces IG-feed-video→Reel normalization notes, off-ratio carousel slides, and sub-1080px resolution WARNs. Fixed a Windows CLI stdout cp1252 encoding bug (force UTF-8). Did NOT run live write-back (production-sheet mutation left for user to trigger). Presented Unit B for review gate.
**Context**: CONSTRUCTION — Code Generation (Unit B) + Build and Test

---

## Write-back column rename
**Timestamp**: 2026-07-25T02:25:00Z
**User Input**: "Change the column name for the audit results to \"Audit Results\" instead of \"revision (claude)\"."
**AI Response**: Renamed the write-back column to "Audit Results". Updated SHELL_HEADERS (new calendars), the reader alias (field `audit_results` accepting "audit results" + legacy "revision (claude)"/"revision" for backward compat), audit.py (column lookup + all emit/docstring text), server.py tool docstring, cli.py help, CLAUDE.md. 33 tests still pass; confirmed the field resolves on live Q3_2026 (col 22 header already reads "Audit Results").
**Context**: CONSTRUCTION — post-build refinement

---

## Remove legacy write-back alias
**Timestamp**: 2026-07-25T02:32:00Z
**User Input**: "Remove the \"revision (claude)\" backwards compatibility"
**AI Response**: Dropped the legacy "revision (claude)"/"revision" spellings from the `audit_results` alias — it now accepts only "audit results". 33 tests pass; live Q3_2026 still resolves (header "Audit Results", col 22).
**Context**: CONSTRUCTION — post-build refinement

---
