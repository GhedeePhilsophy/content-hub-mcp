# Requirements Verification Questions — Preview Platform Simulators

**Feature**: true-to-life Instagram / Facebook / TikTok feed simulators in the social-calendar
preview page.

**Your request**: *"Enhance the preview mode for the social calendar to have true to life
simulators for: Instagram, Facebook, TikTok. I want to be able to click on the simulator for
each of the 3 platforms and see a realistic view of what the feed would look like for that
calendar."*

**How to answer**: fill in the letter after each `[Answer]:` tag, directly in this file. If none
of the options fit, choose the last option (Other) and describe what you want. Tell me when
you're done.

---

## What already exists (context for your answers)

The preview page today already renders each post in its platform's chrome — an Instagram feed
card, a Facebook post, a TikTok 9:16 frame with the action rail — but each one sits inside a
**status-framed review card** (coloured border, Row ID, date, format badge, status dropdown,
copy-caption buttons), grouped by week. There is also an **"IG Grid"** view that shows the
Instagram posts as a 3-column profile grid.

What does *not* exist is a **simulator**: a device-framed, full-feed view you scroll the way the
audience would, without the review scaffolding.

One technical note that affects a few answers: the preview is a **single self-contained HTML
file** with every image inlined as a data URI. The current Q3_2026 preview is already **~10 MB**.
Rendering every asset a second time for the simulators would roughly double that unless the
simulator reuses the images already embedded (which is doable, and is my default plan).

---

## Question 1
How should the simulators be reached from the page?

A) Three new chips in the existing filter row — "📱 IG Simulator", "📱 FB Simulator",
"📱 TikTok Simulator" — sitting alongside the current All / Instagram / Reels / Carousels /
Facebook / TikTok / IG Grid chips. Clicking one swaps the page to that simulator, exactly the
way the "IG Grid" chip works today.

B) One "Simulator" chip that opens a full-screen overlay, with Instagram / Facebook / TikTok
tabs inside it to switch platforms. The review feed stays untouched underneath.

C) A dedicated "Simulator" mode toggle at the top of the page (next to the title) that switches
the whole page between "Review" and "Simulator", with a platform switcher shown in simulator
mode.

X) Other (please describe after [Answer]: tag below)

[Answer]: X - Let's do "B", but remove the "IG Grid"

## Question 2
In what order should posts appear inside a simulator feed?

A) **Reverse chronological — newest first.** This is how a real Instagram/Facebook/TikTok feed
and profile actually look. (The existing IG Grid already does this.)

B) **Chronological — oldest first**, following the calendar's publishing order, so you read the
campaign the way it will roll out.

C) **Reverse chronological, but with the week dividers kept** as subtle separators inside the
feed, so you can still tell which week a post belongs to.

X) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 3
Which posts should a simulator show?

A) **Every row for that platform**, regardless of status — the simulator is a preview of the
whole calendar.

B) **Only Approved rows** — the simulator shows what will actually be published, so it reads as
a true forecast of the feed.

C) **Whatever the status filter chips currently select** — the existing status filters (Draft /
Approved / Awaiting Asset / Wiah Review / Needs review / Asset Delivered) keep applying inside
the simulator, so you can flip between "everything" and "approved only" yourself.

X) Other (please describe after [Answer]: tag below)

[Answer]: C

## Question 4
How much device realism do you want around the feed?

A) **Full phone chassis** — a phone body with rounded corners, notch/dynamic island, and a
simulated status bar (time, signal, wifi, battery), plus the platform's real top bar and bottom
navigation tabs. Most true-to-life; the feed column is narrow (about 390px, like a real phone).

B) **Platform chrome only, no phone body** — the real top bar and bottom nav, at phone width,
but without the device shell around it. Cleaner, slightly wider usable area.

C) **Both** — phone chassis by default, with a toggle to strip it away for a bigger view.

X) Other (please describe after [Answer]: tag below)

[Answer]: C

## Question 5
How should video posts behave in the simulators (Reels and TikTok especially)?

A) **Keep today's behaviour** — show the clip's first frame as a poster with a play button that
opens the clip on Google Drive in a new tab. No change to page size.

B) **Play inline** — embed the actual video so it plays inside the simulator like a real Reel or
TikTok. Most realistic, but this embeds the video bytes into the HTML file and would make the
page dramatically larger (tens of MB per clip) — likely too big to share by email.

C) **Play inline by streaming from Drive** — the simulator points at the Drive-hosted clip
rather than embedding it, so the file stays small, but video only plays for viewers who are
signed in to Google and online.

X) Other (please describe after [Answer]: tag below)

[Answer]: C

## Question 6
The Instagram simulator can show more than one surface. Which do you want?

A) **Feed only** — the scrolling home feed of posts.

B) **Feed + Profile grid** — a tab bar inside the IG simulator switching between the scrolling
feed and the 3-column profile grid. (This would absorb the existing "IG Grid" chip into the
simulator.)

C) **Feed + Profile grid + Reels tab** — as B, plus a Reels-only vertical surface for the
Reel-format rows.

X) Other (please describe after [Answer]: tag below)

[Answer]: C

## Question 7
Inside a simulator, how visible should the review information be (Row ID, status, date)?

A) **Hidden entirely** — the simulator is pristine, exactly what a follower would see. You use
the normal review feed when you need the metadata.

B) **On hover** — the feed looks pristine, but hovering a post reveals a small overlay with its
Row ID, date and status, so you can spot which calendar row you're looking at.

C) **Always visible but subtle** — a small status dot and Row ID tucked into the corner of every
post, so you can scan approval state while reading the feed.

X) Other (please describe after [Answer]: tag below)

[Answer]: B

## Question 8
Realistic feeds have engagement numbers (likes, comments, views, timestamps like "2h"). What
should the simulators show?

A) **Nothing** — no counts at all. Icons only, as the preview does today. Never misleading.

B) **Zeroed / placeholder** — show the count row with dashes or zeros, so the layout is
true-to-life without inventing numbers.

C) **Plausible fake numbers** — invent realistic-looking counts so the feed reads exactly like a
real one. (Worth flagging: fabricated engagement numbers in a review artifact can be mistaken
for real performance data by anyone who sees the page later.)

X) Other (please describe after [Answer]: tag below)

[Answer]: C

---

## Extension opt-in questions

*(These are standard AI-DLC questions. Your previous answers for the audit feature were: Security
— No, Resiliency — No, Property-Based Testing — Partial. Answer the same way unless this feature
changes your view; this one is browser-side HTML/CSS/JS rendering with no new I/O.)*

## Question: Security Extensions
Should security extension rules be enforced for this project?

A) Yes — enforce all SECURITY rules as blocking constraints (recommended for production-grade applications)

B) No — skip all SECURITY rules (suitable for PoCs, prototypes, and experimental projects)

X) Other (please describe after [Answer]: tag below)

[Answer]: B

## Question: Resiliency Extensions
Should the resiliency baseline be applied to this project?

A) Yes — apply the resiliency baseline as directional best practices and design-time guidance (recommended for business-critical workloads, as an informed starting point that you can validate and harden before go-live)

B) No — skip the resiliency baseline (suitable for PoCs, prototypes, and experimental projects where rapid iteration matters more than reliability)

X) Other (please describe after [Answer]: tag below)

[Answer]: B

## Question: Property-Based Testing Extension
Should property-based testing (PBT) rules be enforced for this project?

A) Yes — enforce all PBT rules as blocking constraints (recommended for projects with business logic, data transformations, serialization, or stateful components)

B) Partial — enforce PBT rules only for pure functions and serialization round-trips (suitable for projects with limited algorithmic complexity)

C) No — skip all PBT rules (suitable for simple CRUD applications, UI-only projects, or thin integration layers with no significant business logic)

X) Other (please describe after [Answer]: tag below)

[Answer]: B
