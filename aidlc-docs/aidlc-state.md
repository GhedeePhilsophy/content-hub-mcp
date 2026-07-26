# AI-DLC State

**Project**: content-hub-mcp — Content Hub MCP server (Social Calendar workflow)
**Type**: Brownfield
**Feature**: Social Calendar post audit (asset + caption compliance vs. per-platform / per-post-type standards)

## Current Phase
**CONSTRUCTION** → Units A & B code-generated and validated (mock). At the Unit B review gate;
live sheet write-back not yet run (deliberate — a production-sheet mutation for the user to trigger).

## Stage Ledger
| Phase | Stage | Status |
|-------|-------|--------|
| INCEPTION | Workspace Detection | ✅ Complete |
| INCEPTION | Reverse Engineering | ⏭️ Skipped as standalone — did targeted RE of the audit-relevant subsystem instead (logged in audit.md); full RE artifacts not needed for a scoped single-package feature |
| INCEPTION | Requirements Analysis | ✅ Approved 2026-07-25 |
| INCEPTION | User Stories | ⏭️ Skipped — internal tooling, single stakeholder, requirements already carry acceptance criteria |
| INCEPTION | Workflow Planning | 🔄 In progress |
| INCEPTION | Application Design | ⏭️ Folded into taxonomy analysis + requirements §3.1 (no new service layer) |
| INCEPTION | Units Generation | ✅ Two units identified (A: specs+generation, B: audit) — see Notes |
| CONSTRUCTION | Functional Design (Unit A) | ✅ Captured in taxonomy analysis |
| CONSTRUCTION | Code Generation (Unit A) | ✅ Complete — specs.py + plan_visual refactor + tests (19 pass), dry-run clean. Awaiting review gate before Unit B. |
| CONSTRUCTION | Code Generation (Unit B) | ✅ Complete — audit.py + tool + CLI; dry-run & mock validated on live Q3_2026. Awaiting review gate. |
| CONSTRUCTION | Build and Test | ✅ 33 property tests pass; CLI dry-run→mock clean. (live write-back left for the user to run deliberately.) |

## Extension Configuration
| Extension | Enabled | Scope |
|-----------|---------|-------|
| Security Baseline | **No** | Read-only local tool, already-authorized session, no new attack surface. |
| Resiliency Baseline | **No** | Not a deployed cloud workload; per-row fault tolerance covered by NFR-5. |
| Property-Based Testing | **Partial** | Enforce PBT for pure functions only: `specs` resolution, aspect-from-dimensions, caption checks. Introduces `pytest` + `hypothesis` + `tests/` (repo has none today). I/O glue validated via the CLI dry-run/mock harness, not PBT. |

## Notes
- Scope now spans two related workstreams (candidate units for Workflow Planning):
  - **Unit A — canonical specs + generation correction:** new `content_hub/social/specs.py`
    (`SPECS[platform][type]` + validity/normalization map, dated/sourced) and a `rules.plan_visual`
    refactor to consume it (adds `platform` param; IG/TikTok non-Reel video → 9:16, FB 16:9 kept).
    Touches generation — has its own test/blast-radius surface (existing IG/TikTok video assets are
    not auto-regenerated; audit flags them).
  - **Unit B — the audit:** `content_hub/social/audit.py` + `social_audit_calendar` MCP tool + CLI
    subcommand, consuming Unit A's specs and reusing `core.drive`, `calendar`, exporter-style row
    resolution and the preview's asset cache.
- Unit B depends on Unit A (shared specs). Build A first.

## Key decisions
- 2026-07-25: Delivery = tool + sheet write-back; Inspection = download & inspect real assets;
  Captions = objective limits only; Standards = curated in-code specs table (dated + sourced).
- 2026-07-25: Stories out of scope.
- 2026-07-25: Taxonomy correction = **shared source of truth + fix generation** (canonical
  `specs.py`; `plan_visual` refactored to consume it).
