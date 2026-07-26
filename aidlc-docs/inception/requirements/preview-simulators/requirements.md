# Requirements — Preview Platform Simulators

**Feature**: true-to-life Instagram / Facebook / TikTok feed simulators in the social-calendar
preview page.
**Date**: 2026-07-26
**Depth**: Standard
**Source answers**: [requirement-verification-questions.md](requirement-verification-questions.md)

---

## 1. Intent analysis

| Dimension | Assessment |
|---|---|
| **User request** | *"Enhance the preview mode for the social calendar to have true to life simulators for: Instagram, Facebook, TikTok. I want to be able to click on the simulator for each of the 3 platforms and see a realistic view of what the feed would look like for that calendar."* |
| **Request type** | New Feature (enhancement of an existing module) |
| **Request clarity** | Clear in goal, under-specified in interaction detail → resolved by the 8 answered questions |
| **Scope estimate** | Single Component — `content_hub/social/preview.py`, plus doc updates in `README.md` / `CLAUDE.md` |
| **Complexity** | Moderate — self-contained HTML/CSS/JS rendering; no new Python I/O, no new dependency, no API/credit spend; real trade-offs in page weight and video fidelity |

**Existing state.** The preview already renders per-post platform chrome (IG feed card, FB post,
TikTok 9:16 + action rail) inside status-framed review cards grouped by week, plus a separate
"IG Grid" profile view. What is missing is a **simulator**: a device-framed, full-feed surface
scrolled the way the audience would see it, without review scaffolding.

---

## 2. Decisions taken (from answers)

| # | Question | Answer | Decision |
|---|---|---|---|
| Q1 | Entry point | **X** (B, minus IG Grid) | One **"Simulator" chip** opens a **full-screen overlay** with Instagram / Facebook / TikTok tabs inside. The **existing "IG Grid" chip and `#grid` section are removed** — the grid is absorbed into the IG simulator's Profile tab. |
| Q2 | Feed order | **A** | Reverse chronological, newest first. No week dividers inside a simulator. |
| Q3 | Which posts | **C** | The page's existing **status filter chips keep applying** inside the simulator. |
| Q4 | Device realism | **C** | **Phone chassis by default** (body, notch, simulated status bar) **plus a toggle** to strip the chassis for a larger view. |
| Q5 | Video | **C** | **Play inline by streaming from Drive** — reference the Drive-hosted clip; do not embed video bytes. |
| Q6 | IG surfaces | **C** | IG simulator has three tabs: **Feed · Profile grid · Reels**. |
| Q7 | Review metadata | **B** | Feed reads pristine; **hovering a post** reveals Row ID, date and status. |
| Q8 | Engagement numbers | **C** | **Plausible invented counts** so the feed reads like a real one. No disclaimer label — the audience is a small team that knows what it is looking at. |
| Ext | Security baseline | **B** | Not enforced. |
| Ext | Resiliency baseline | **B** | Not enforced. |
| Ext | Property-based testing | **B** | **Partial** — PBT for pure functions only. |

---

## 3. Functional requirements

### FR-1 — Simulator entry point
A single **"Simulator"** chip in the existing filter row opens a full-screen overlay above the
review page. The review feed underneath is left mounted and unchanged; closing the overlay
(× button, `Esc`, or backdrop click) returns to exactly the prior scroll position and filter
state.

*Acceptance*: clicking the chip opens the overlay; `Esc` closes it; the review feed's scroll
position and active filters are unchanged after closing.

### FR-2 — Platform tabs
The overlay carries three tabs — **Instagram · Facebook · TikTok** — each rendering that
platform's feed. The tab bar shows each platform's post count for the current status filter.
Switching tabs does not rebuild the page or refetch anything.

*Acceptance*: all three tabs render; each shows only its own platform's rows; counts match the
review feed's per-platform counts under the same status filter.

### FR-3 — Removal of the IG Grid view
The standalone **"IG Grid" chip and its `#grid` section are deleted**. Its functionality moves
to the Instagram simulator's **Profile** tab (FR-6). No other view loses functionality.

*Acceptance*: no "IG Grid" chip remains; the profile grid is reachable only inside the IG
simulator; `_grid_cell` rendering (thumbnail, carousel/reel corner badge, status frame) is
preserved there.

### FR-4 — Feed ordering
Posts within every simulator surface are ordered **reverse chronological (newest first)** by the
row's Date. Rows with no parseable date sort last. No week dividers appear inside a simulator.

*Acceptance*: the first post in each feed is the latest-dated row for that platform.

### FR-5 — Status filtering carries through
The page's existing status filter chips (`All statuses / Draft / Approved / Awaiting Asset /
Wiah Review / Other / Asset Delivered / Needs review`) continue to apply inside the simulator.
Changing the filter updates the simulator's contents live.

*Acceptance*: with "Approved" selected, only Approved rows appear in every simulator surface and
in the tab counts.

### FR-6 — Per-platform surfaces
- **Instagram** — three sub-tabs:
  - **Feed** — scrolling home feed: header (avatar, handle, ⋯), media at its true aspect ratio,
    action row (heart / comment / share / bookmark), engagement line, caption with the
    `…more` fold at 125 characters, first-comment hashtags.
  - **Profile** — the 3-column grid (absorbed from the removed IG Grid view), with the profile
    header (avatar, handle, post count, bio).
  - **Reels** — a vertical, full-bleed 9:16 surface showing only `Format = Reel` rows.
- **Facebook** — feed: header (page name, date, globe icon), caption **above** the media,
  reaction bar, like/comment/share counts.
- **TikTok** — vertical full-bleed 9:16 feed, one post per screen with scroll-snap, the right
  action rail (avatar, heart, comment, bookmark, share), and the bottom caption block
  (handle, caption, music line).

*Acceptance*: each surface matches its platform's real layout conventions; the Reels tab shows
exactly the rows the review page's "Reels" chip counts.

### FR-7 — Device chassis with toggle
Every simulator renders inside a **phone chassis** by default — device body with rounded
corners, notch/dynamic island, and a simulated status bar (time, signal, wifi, battery) — at
realistic phone width (~390px). A toggle in the overlay header **strips the chassis**, widening
the feed column. The choice persists across tab switches within a session.

*Acceptance*: chassis renders by default; the toggle removes/restores it; switching platform
tabs keeps the chosen mode.

### FR-8 — Video posts ~~play inline~~ → **SUPERSEDED 2026-07-26: poster + Drive link**

> **Original requirement** (from Q5 = C): video posts play inline in the simulators, streaming
> from Drive, degrading to poster + Drive link where unavailable.
>
> **Why it changed.** Both embed routes were built and measured against the real assets, and
> neither is viable:
> - **Native `<video>`** against `drive.usercontent.google.com/download` — the URL serves
>   `video/mp4` correctly to an anonymous request, but a *browser* sends `Origin: null` from
>   any page opened off disk, and Drive answers **403 with no `Access-Control-Allow-Origin`**.
>   The non-CORS path is separately blocked by **`Cross-Origin-Resource-Policy: same-site`**.
>   Both doors are shut, by Google's policy, not by anything in this codebase.
> - **Drive's `/file/d/<id>/preview` player** — works, and was confirmed working in a local
>   browser. But it is a player *page*: it draws its own toolbar above the picture, which reads
>   as a black band across the top of a feed post. Cropping it would also push its playback
>   controls out of the frame.
>
> **Decision** (user, 2026-07-26, presented with all three options): **drop inline playback.**

Video posts show the clip's already-extracted poster frame with a play button that **opens the
clip on Drive in a new tab** — the pre-existing behaviour, needing no simulator-specific code
since the anchor comes across in the cloned node. A paused feed shows stills anyway, so this
costs little realism.

*Acceptance*: a video post renders its poster frame with no black band and no embed; the play
button opens the clip on Drive.

### FR-9 — Hover-revealed review metadata
Feed posts read pristine. **Hovering** a post reveals a small overlay showing **Row ID, date and
status** (status colour-coded with the existing palette). The overlay must not shift layout or
obscure the media while inactive.

*Acceptance*: no review metadata is visible at rest; hover reveals it; it disappears on mouse-out.

### FR-10 — Simulated engagement numbers
Each post displays plausible invented engagement counts (likes, comments, shares/views, and a
relative timestamp such as "2h"), styled per platform.

- Numbers are **deterministic** — derived from the Row ID — so the same post shows the same
  counts on every rebuild. (Random-per-build numbers would read as a bug.)
- Magnitudes are plausible for the account's scale and consistent across surfaces (a post's like
  count is the same in the IG Feed and Profile tabs).
- **No disclaimer label.** The page is reviewed by a small internal team who know the simulator
  is a mockup.

*Acceptance*: counts are stable across two consecutive builds of the same calendar; the same row
shows the same count in every surface it appears in.

### FR-11 — No regression to existing behaviour
The review feed, week grouping, status pills, per-card action buttons, carousel navigation,
back-to-top/current-week widgets, and the **Apps Script live status-editing path**
(`setPostStatus` / `getPostStatuses` / `getViewerInfo`) all continue to work unchanged.

*Acceptance*: status editing via the Apps Script web app still writes to the living sheet; a
status changed in the sheet is still reflected on load.

---

## 4. Non-functional requirements

- **NFR-1 — Single self-contained file.** The page must remain one portable HTML file, openable
  offline from `file://`, with no external stylesheet, script, or font. (Video streaming is the
  one deliberate exception, per FR-8, and degrades gracefully offline.)
- **NFR-2 — Page weight must not materially grow.** The Q3_2026 preview is already ~10 MB.
  Simulators must **reuse the data URIs already inlined** for the review feed rather than
  emitting a second copy of every asset. Target: **< 15% growth** in output size.
- **NFR-3 — stdout stays clean.** Per the project invariant, all progress goes to stderr via
  `emit`; the builder returns a structured dict. No new stdout writes.
- **NFR-4 — No new spend or I/O.** The feature adds no API calls, no credit spend, no extra
  Drive downloads. It renders from data already fetched for the review feed.
- **NFR-5 — No new Python dependency.** HTML/CSS/JS only, built by the existing `preview.py`.
- **NFR-6 — Theme-aware and responsive.** Simulators honour the existing light/dark handling and
  remain usable on a narrow window (the overlay must never scroll the page body horizontally).
- **NFR-7 — Accessibility.** The overlay is keyboard-navigable: `Esc` closes, tabs are reachable
  by keyboard, and the chassis toggle is a real focusable control.

---

## 5. Assumptions, risks and open items

**ASSUMPTION-1 — Status filter control inside the overlay.** Q3 says the status chips keep
applying, but Q1's full-screen overlay covers those chips. I am assuming the overlay header
carries a **compact mirror of the status filter**, two-way bound to the chips underneath, so you
can change it without closing the simulator. *Object at the approval gate if you would rather
the overlay simply inherit whatever was selected when it opened.*

**ASSUMPTION-2 — Drive streaming mechanism.** The most reliable inline-playback path for a
link-shared Drive file is Drive's own **iframe player** (`/file/d/<id>/preview`), not a raw
`<video src>`. The repo's `exporters/__init__.py` link-style table is about *scheduler fetches*
and documents no iframe form, so this must be **verified during construction, measured not
assumed** — consistent with how that table was built. FR-8's fallback exists precisely because
this may not survive every context (local `file://`, the Apps Script sandbox, an unauthenticated
viewer).

**DECIDED — Simulated engagement numbers (Q8 = C, confirmed 2026-07-26).** The simulators show
plausible invented counts, with **no disclaimer label**. Rationale given: the preview is reviewed
by a small internal team who know the simulator is a mockup. Determinism (FR-10) remains
required — not for honesty, but because counts that changed on every rebuild would read as a bug.

**RISK-2 — Page-weight regression.** Naïvely re-rendering assets into three more surfaces would
roughly double a 10 MB file. NFR-2 is the guard; the implementation must share image references
(single emission, referenced by id / cloned at runtime) rather than duplicating data URIs.

**RISK-3 — Removing IG Grid is a deletion.** Per Q1 the standalone grid view goes away. Anyone
with a bookmarked habit of that chip will need to reach it via the IG simulator's Profile tab.
Confirmed as intended.

---

## 6. Out of scope

- Stories surfaces (consistent with `specs.py`: the calendar's Format enum is Post / Reel /
  Carousel and does not schedule Stories).
- Editing content from inside a simulator (status editing stays in the review feed and the sheet).
- Any change to generation, the audit, exporters, or the living sheet's schema.
- Platforms beyond Instagram / Facebook / TikTok.

---

## 7. Summary

A single **Simulator** chip opens a full-screen, phone-framed overlay with **Instagram
(Feed / Profile / Reels) · Facebook · TikTok** tabs, showing the calendar's posts newest-first,
respecting the page's status filters, with hover-revealed Row ID / date / status, deterministic
simulated engagement counts, and inline video streamed from Drive with a graceful offline
fallback. The standalone IG Grid view is removed and absorbed into the IG Profile tab. The page
stays a single portable file, grows less than 15%, spends nothing, and leaves the Apps Script
live-status-editing path untouched.
