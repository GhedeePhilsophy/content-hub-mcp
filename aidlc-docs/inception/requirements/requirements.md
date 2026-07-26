# Requirements — Social Calendar Post Audit

**Feature**: Audit each social-calendar post's assets and caption for compliance with the
current per-platform, per-post-type standards, and surface the findings where the team works.
**Phase**: INCEPTION → Requirements Analysis
**Depth**: Standard
**Status**: Draft — awaiting approval

---

## 1. Intent

Give the Content Hub a way to answer, per post: *"Is this asset and this caption actually fit
for where it's going to be published?"* — checked against the **latest** Instagram / Facebook /
TikTok specs for that specific **post type** (Reel, video Post, image Post, Carousel), before the
round is approved and exported to a scheduler.

Today nothing verifies the *delivered* asset. `rules.plan_visual` fixes the *intended* aspect
ratio at generation time, but a bring-your-own ("Created Asset Link") file, a re-generated file,
or a hand-edited caption can drift from spec and only be caught by eye in the preview. This
feature closes that gap with an objective, repeatable check.

---

## 2. Scope

### In scope
- A new **canonical spec module** `content_hub/social/specs.py` — the single `SPECS[platform][type]`
  source of truth for platform standards + a validity/normalization map (see §3.1 and the taxonomy
  analysis). **Both** the audit and generation read it.
- A **refactor of `rules.plan_visual`** to consume `specs` and take a `platform` parameter, fixing
  IG/TikTok non-Reel video → 9:16 while preserving Facebook 16:9 (decided 2026-07-25 — "shared
  source of truth + fix generation"). Single call site (`calendar._build_job`); `VisualPlan` shape
  unchanged.
- A new **`social_audit_calendar`** MCP tool + matching **CLI subcommand** + an `audit.py` module
  in `content_hub/social/`.
- Auditing the **four characteristics** the user named, per post:
  1. Asset **aspect ratio**
  2. Asset **resolution** (pixel dimensions)
  3. Asset **file size**
  4. Caption **size/content** (objective limits — see §5)
- Evaluated against **Platform** (Instagram / Facebook / TikTok) × **Post type**
  (Reel / video Post / image Post / **Carousel**).
- **Downloading and inspecting the real asset bytes** from Drive for each row that has an asset.
- A **curated, dated, sourced in-code specs table** as the standard of record.
- **Writing findings back** into the living sheet per row (guardrail-respecting), plus the
  structured return value.

### Out of scope (this feature)
- AI / LLM judgement of caption tone or "appropriateness" (objective checks only — decided).
- Fetching standards live from the web at runtime (specs are curated in code — decided).
- Auto-fixing / re-generating failing assets (audit reports; it does not mutate assets).
- Changing Drive permissions, Status values, or the export logic.
- Blog / Email workflows (not built yet).

---

## 3. Post-type taxonomy (how a row maps to an audited type)

The sheet describes a post with **Platform** + **Format** (`Post` / `Reel` / `Carousel`) +
**Visual Type**. The audit resolves each row to exactly one audited post type:

| Audited post type | Derived from | Notes |
|---|---|---|
| **Reel** (vertical video) | `Format = Reel` | 9:16. Covers AI `text-to-video` reels and `Recorded video of Wiah`. |
| **Video Post** (feed video) | `Format = Post` + video Visual Type | 16:9 / feed clip. |
| **Image Post** | `Format = Post` + image Visual Type | 1:1 or 4:5 single image. |
| **Carousel** | `Format = Carousel` | Multi-slide; every slide is audited; the set shares one spec. |

- **FR-1** The audit MUST classify every row with a Row ID into one of these types (or mark it
  *unclassifiable* with a reason, never silently drop it).
- **FR-2** TikTok is short-form vertical video only; an image/carousel row targeted at TikTok is a
  reportable warning (platform/format mismatch), not a crash.

### 3.1 Canonical specs & platform normalization (from the taxonomy analysis)

- **FR-1a** A single `specs.py` module holds `SPECS[platform][type]` (aspect ratio(s), min
  resolution, max file size, caption cap, hashtag cap, fold length, carousel slide caps) with a
  source citation + `last_verified` date per entry. Both audit and `plan_visual` resolve against it.
- **FR-1b** Platform normalization is encoded, not assumed: **IG/TikTok non-Reel video → 9:16**
  (Facebook feed video → 16:9); **TikTok single image → WARN** ("use Photo Mode carousel");
  **per-platform carousel caps** IG 20 / FB 10 / TikTok 35 (+500 MB).
- **FR-1c** `plan_visual` is corrected to match FR-1b so generation produces platform-correct
  aspect ratios; existing on-Drive assets are not auto-regenerated (idempotency) and surface as
  audit findings instead — see the blast-radius note in the taxonomy analysis.
- **FR-2a** **Facebook text/link-only** posts (no asset): asset checks report **NA (no media)**;
  caption checks still apply. "No asset" is not a failure for these.
- **FR-2b** **Stories are out of scope** (not in the `Format` enum) — documented as a known
  limitation in `specs.py`, revisitable if the calendar starts scheduling them.

---

## 4. Asset checks (FR-3 … FR-7)

For each row with a **Generated Asset Link** (preferred) or **Created Asset Link**:

- **FR-3** Resolve the asset from the sheet link the same way the preview/exporter do
  (`file_id_from_link`); a **carousel** link is a folder → audit each image slide in it.
- **FR-4** **Download the real bytes** from Drive and measure:
  - **aspect ratio** — from actual pixel dimensions (images via Pillow; video first frame /
    metadata via imageio+ffmpeg — both already dependencies).
  - **resolution** — width × height in px (video: frame size).
  - **file size** — from Drive metadata `size` (no second download needed; `list_children`/`get_file`
    already return it).
- **FR-5** Compare each measured value to the resolved post type's spec and produce a per-check
  verdict: **PASS / WARN / FAIL** with a human-readable reason (e.g. *"9:16 asset on an Image Post —
  Instagram wants 4:5 or 1:1"*, *"720×1280 below TikTok's 1080-wide minimum"*, *"58 MB exceeds
  Instagram's 30 MB image cap"*).
- **FR-6 (idempotency / cost):** downloading is per-asset; the audit MUST reuse the preview's
  content-addressed cache pattern (Drive md5/size key) so a re-run only re-downloads assets that
  changed. It MUST NOT call any generation/paid API.
- **FR-7** A row with **no asset**, an unparseable link, a link that no longer resolves, a
  `Failed` marker, or a `Recorded video of Wiah` awaiting footage is reported as **skipped/NA**
  with the reason — never a hard error.

---

## 5. Caption checks (FR-8 … FR-10) — objective only

- **FR-8** Check caption **length** against the platform's caption character cap for that post
  type, and report the **visible-before-"…more" fold** point (e.g. IG feed ≈ 125 chars) so the
  team sees what shows above the fold.
- **FR-9** Check **hashtag count** (from the caption and/or the `First-comment Hashtags (IG)`
  column) against the platform cap; flag empty captions where a caption is expected.
- **FR-10** All caption checks are **deterministic and offline** — no API, no model, repeatable.
  (No tone/brand-voice judgement in this feature.)

---

## 6. Standards of record (FR-11 … FR-13)

- **FR-11** Platform standards live in a **curated in-code specs table**, one threshold set per
  **platform × post type**, carrying for each: allowed aspect ratio(s) (with tolerance), minimum
  resolution, maximum file size, caption char cap, hashtag cap, and the fold length.
- **FR-12** Each spec entry MUST carry a **source citation** (the platform help/spec URL) and a
  **`last_verified` date**. During Construction the concrete numbers will be **verified via web
  search** against the platforms' current published specs and dated accordingly.
- **FR-13** The table is the single place to update when a platform changes its specs; the audit
  logic reads from it and hard-codes no thresholds inline.

---

## 7. Delivery & write-back (FR-14 … FR-17)

- **FR-14** New MCP tool **`social_audit_calendar(calendar_id, mode, statuses=None, …)`** returning
  a structured dict: overall summary counts (`pass/warn/fail/skipped`), and per-row findings
  (row_id, resolved type, measured values, per-check verdicts + reasons).
- **FR-15** Matching **CLI subcommand** (`python -m content_hub.cli social audit <id> --mode …`),
  consistent with the existing harness.
- **FR-16 (write-back):** in `live` mode, write a concise per-row verdict + issue list back into
  the living sheet in a **guardrail-respecting** location that does **not** collide with
  `generate`'s existing `[auto]` Notes line and **never** touches Status or the machine-owned
  Generated Asset Link / Est. Cost / AI Model columns. *(Exact target — the unused
  `Revision (Claude)` column vs. a distinct `[audit]` marker line in Notes — to be settled in
  Functional Design; the requirement is: visible in the sheet, guardrail-safe, non-colliding.)*
- **FR-17** Rows audited default to **all rows that have an asset**, with an optional `statuses`
  filter (mirroring the exporter) so the team can, e.g., audit only `Approved` rows before export.

---

## 8. Modes (FR-18)

- **FR-18** The tool honors the project's three-mode contract:
  - **dry-run** — resolve rows, classify, and report *what would be checked* (and read specs), but
    do **not** download assets or write to the sheet. Plan only.
  - **mock** — safe rehearsal: perform the read-only inspection but route any write-back to the
    safe mock destination / never touch the production sheet.
  - **live** — download + inspect the real assets and write findings back to the living sheet.
  - *(If, in design, it turns out the audit spends nothing and downloads are harmless reads, `mock`
    may reduce to "inspect but don't write" — to be confirmed in Functional Design, consistent with
    how `edit`/`add` collapse `mock` into `dry-run`.)*

---

## 9. Non-functional requirements (project invariants — all MUST hold)

- **NFR-1** `core`/engine code never writes to **stdout** and never calls `sys.exit`; all progress
  goes to **stderr** via an `emit` callback. Tools return structured dicts. (stdout = MCP channel.)
- **NFR-2** **Row ID is the stable key**; findings address rows by Row ID and survive reordering.
- **NFR-3** **Read-only w.r.t. assets**: the audit never regenerates, deletes, moves, or re-permissions
  Drive files, and never spends credits.
- **NFR-4** **Deterministic**: given the same sheet + same assets + same specs table, the audit
  produces the same verdicts (no network-derived standards, no LLM).
- **NFR-5** **Resilient per row**: one bad/unfetchable asset is reported and skipped; it never kills
  the batch (matches `run_batch` behaviour).
- **NFR-6** Reuses existing primitives (`core.drive`, `social.rules`, `social.calendar`,
  exporter-style row resolution, preview's cache pattern) rather than duplicating them.
- **NFR-7** Windows-friendly; no new heavyweight dependency (Pillow + imageio/ffmpeg already present).

---

## 10. Extension configuration (opt-in — please decide at approval)

| Extension | Recommendation | Rationale for this feature |
|---|---|---|
| **Security Baseline** | **Skip (B)** | Read-only local tool over an already-authorized Google session; no new attack surface, secrets handling, or external input beyond the team's own sheet/Drive. |
| **Resiliency Baseline** | **Skip (B)** | AWS Well-Architected reliability guidance targets deployed cloud workloads; this is a stdio CLI/MCP tool. Per-row fault tolerance is already captured in NFR-5. |
| **Property-Based Testing** | **Partial (B)** — *worth considering* | The spec-comparison + aspect-ratio-from-dimensions + caption-length logic are pure functions with clear properties; PBT would strengthen them. There is currently **no test suite** in the repo, so this would introduce one. |

*(These are the three extensions the workflow found. Choose Yes/Partial/No for each at the approval
gate; default recommendation shown.)*

---

## 11. Assumptions

- **A-1** The living Google Sheet already exists and is the source of truth; audit reads it live
  (like `edit`/`export`), or a versioned snapshot if a version is later requested.
- **A-2** Assets that are link-shared remain fetchable by the authorized OAuth session (same access
  the preview relies on).
- **A-3** "Video Post" and "Image Post" correspond to `Format = Post` disambiguated by Visual Type;
  "Reel" = `Format = Reel`. Carousel is added as a fourth audited type though not named in the
  request. Please correct if the taxonomy differs.
- **A-4** The current specs numbers will be web-verified during Construction; the requirement fixes
  the *structure and sourcing*, not the literal thresholds (those are Construction detail).

---

## 12. Acceptance criteria

1. `social_audit_calendar('<id>', mode='dry-run')` classifies every row and lists what it would
   check, touching nothing.
2. In `live` mode it downloads each asset, reports true aspect ratio / resolution / file size and a
   PASS/WARN/FAIL per check against the correct platform×type spec, and reports caption length +
   hashtag-count verdicts + fold preview.
3. A carousel row audits each slide; a TikTok/image mismatch, a missing asset, and an oversize file
   are each reported (not crashed).
4. Findings are written back to the sheet per row without touching Status or machine-owned columns
   and without colliding with `generate`'s `[auto]` note.
5. The specs table has a citation + `last_verified` date per entry, verified against current
   platform docs.
6. No stdout writes from engine code; structured dict returned; no credits spent; re-run reuses the
   asset cache.
