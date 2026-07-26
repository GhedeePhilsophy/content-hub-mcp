# Operations — Preview Platform Simulators

**Stage status**: **PLACEHOLDER.** Operations is a placeholder in this AI-DLC workflow, and it
is genuinely not applicable here — there is nothing deployed. This feature produces a **static
HTML file**: no service, no infrastructure, no runtime to monitor, no rollback procedure beyond
`git revert`.

What would normally live in this stage maps onto these instead:

| Operations concern | How it is handled here |
|---|---|
| Deployment | `python -m content_hub.cli social preview <id>` uploads the page to `00_Calendar & Docs` on Drive, replacing the same-named file in place. Rebuild and re-run to redeploy. |
| Rollback | Rebuild from the previous commit, or from a frozen `.xlsx` snapshot via the positional version argument. The artifact is disposable and regenerable at any time. |
| Monitoring | None. The page is static and has no telemetry. Problems surface when a reviewer opens it. |
| Alerting | None. |
| Incident response | Rebuild; if the builder itself is broken, `git revert` the commit — the change is confined to one file. |

## Release checklist for this feature

- [x] Unit tests pass (59)
- [x] Automated integration checks pass (JS syntax gate, page contract 8/8, structural 8/8)
- [x] Warm and cold builds succeed
- [x] NFR-2 measured: −10.2% vs baseline (budget allowed +15%)
- [x] Video handling resolved — inline playback removed after measurement (FR-8 superseded);
      video posts show the poster frame with a play button opening the clip on Drive
- [ ] Manual browser scenarios 3 and 5 (remaining simulator sub-checks, Apps Script non-regression)
- [ ] Commit / PR / merge
- [ ] Publish the page to Drive (a run without `--no-publish`)

## Known operational notes

- **Assets must stay link-shared.** The page never changes Drive permissions (same invariant the
  exporters follow), and a video post's play button opens the clip on Drive — which fails for a
  viewer if sharing is later tightened.
- **Do not re-attempt inline video embedding.** It was built, measured and removed: Drive
  answers a browser's `Origin: null` with `403` and no `Access-Control-Allow-Origin`, and blocks
  the no-cors path with `Cross-Origin-Resource-Policy: same-site`. Probing those URLs with a
  script that sends no `Origin` header returns a misleading success. See the code summary.
- **The page is a snapshot.** Statuses are re-hydrated live when served through the Apps Script
  web app, but everything else (captions, assets, engagement counts) is frozen at build time.
  Rebuild after a content round.
- **Engagement counts are simulated**, derived deterministically from Row ID. They are stable
  across rebuilds by design. Re-scale via `_ENGAGE_LIKES` / `_ENGAGE_VIEWS` in `social/preview.py`.
