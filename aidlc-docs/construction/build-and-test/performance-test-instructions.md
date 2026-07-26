# Performance Test Instructions

**Applicability.** There is no server, no request path and no concurrency here — the standard
load/stress/throughput model does not apply, and writing fake numbers against it would be
worse than saying so. Performance for this feature means three concrete things, all of which
are measurable and one of which is a hard requirement:

1. **Artifact size** — the binding constraint (NFR-2)
2. **Build duration** — how long a rebuild takes
3. **Client-side responsiveness** — whether the simulator stalls the browser

---

## 1. Artifact size (NFR-2) — the hard gate

**Requirement**: the simulators must not materially grow the page. Budget was **≤ +15%**
against the pre-feature baseline.

```powershell
python -m content_hub.cli social preview Q3_2026 --no-publish
python -c "import pathlib; print(pathlib.Path('Ghedee_Social_Calendar_Q3_2026_preview.html').stat().st_size)"
```

| Measure | Value |
|---|---|
| Baseline (pre-feature, 120 posts) | 10,399,175 bytes |
| Current | 9,335,823 bytes |
| Delta | **−1,063,352 (−10.2%)** |
| Budget | ≤ +15% growth |
| **Status** | **PASS — the page shrank** |

**Why it shrank**: the simulators emit no image bytes (surfaces are built by cloning media
nodes already in the review feed), while removing the standalone IG Grid deleted a second,
340px encoding of every Instagram asset.

**Regression watch**: if this number jumps by megabytes, something started rendering assets
server-side again. That is the failure this measurement exists to catch — re-run it after any
change to `preview.py`'s rendering.

---

## 2. Build duration

```powershell
# warm (thumbnail cache hit)
Measure-Command { python -m content_hub.cli social preview Q3_2026 --no-publish }

# cold (re-download + re-encode every asset)
Measure-Command { python -m content_hub.cli social preview Q3_2026 --no-publish --no-cache }
```

| Scenario | Measured (120 posts, 106 cached assets) |
|---|---|
| Warm build | a few seconds |
| Cold build (`--no-cache`) | **~2m 26s** |

Cold-build time is dominated by Drive downloads and JPEG re-encoding, not by the simulator
work. The feature did not measurably change build time — it adds string assembly only.

---

## 3. Client-side responsiveness (manual)

The simulator builds DOM in the browser, so the risk is a stall on open or on tab switch.

**How to measure**: open the built page, DevTools → Performance, record while clicking
**Simulator**, then while switching platform tabs.

| Check | Target |
|---|---|
| Overlay open → first paint of the feed | no perceptible stall |
| Platform / sub-tab switch | no perceptible stall |
| Scrolling a feed surface | smooth; snap-scroll on TikTok/Reels feels native |
| Memory | no unbounded growth when switching tabs repeatedly |

**Design note relevant to this**: only the *visible* surface is ever built — the simulator never
constructs all five at once. Surfaces are rebuilt on each tab switch rather than memoized;
that was measured as instant at this calendar's size (120 posts). **If a future calendar is
several times larger and tab switching becomes visibly slow, memoization is the known fix** —
it was deliberately left out as premature (recorded as a deviation in the code-generation plan).

Video iframes are mounted only when the viewer presses play, so a feed of ~100 video posts
never pre-loads ~100 players.

---

## If performance does not meet requirements

1. **Size regressed** — find what is emitting image bytes twice; the simulator must clone, not
   render. Check `_data_uri` call sites.
2. **Tab switching slow** — add the per-surface memoization described above, keyed by
   platform + sub-tab + status filter, invalidated on filter change.
3. **Cold build slow** — expected; it is network-bound. The md5-keyed thumbnail cache exists
   precisely so this is a rare cost.
