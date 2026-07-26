# Post-Type Taxonomy — Coverage Analysis

**Question (from construction):** Does the current audit taxonomy (Reel / Video Post / Image
Post / Carousel) cover all possible post types on Instagram, Facebook, and TikTok?

**Verdict:** **Partially.** The four buckets match the calendar's own `Format` enum
(`Post` / `Reel` / `Carousel`), but they are *not* self-sufficient:

1. **A bucket is not a spec.** "Carousel" and "Video Post" resolve to different aspect ratios,
   slide caps, and file-size caps on each platform. Specs MUST key on **platform × type**, never
   type alone. (Already an NFR; this analysis makes the concrete cells explicit.)
2. **Some platform × type combinations are invalid or must be normalized** (see matrix) — e.g.
   Instagram has no separate feed video (it publishes as a Reel), and TikTok has no single-image
   post (images exist only as Photo Mode carousels).
3. **Real post types fall outside the taxonomy:** Stories (all three), TikTok **Photo Mode** as a
   distinct-from-IG carousel, and Facebook **text/link-only** posts (no asset).

Data verified July 2026 against platform-spec references (see Sources).

---

## Full universe of schedulable, media-bearing post types (2026)

| Platform | Native post types relevant to a content calendar |
|---|---|
| **Instagram** | Single image (feed), Carousel (up to 20, mixed media), **Reels** (feed video *is* a Reel), Stories. *(Live, Guides, Notes — not scheduled media, out of scope.)* |
| **Facebook** | Text/status, Link post, Single image, **Video Post** (feed, landscape OK, up to 240 min), **Reels** (distinct from feed video), Carousel, Stories. *(Live — out of scope.)* |
| **TikTok** | Video (single vertical type; TikTok has no "Reel" label and no landscape feed video), **Photo Mode** carousel (4–35 images), TikTok Stories. *(LIVE — out of scope.)* |

---

## Validity / normalization matrix — sheet `Format` × `Platform`

How each `Platform` + `Format` (+ Visual Type) row SHOULD be audited:

| Sheet row | Instagram | Facebook | TikTok |
|---|---|---|---|
| **Post + image** → *Image Post* | ✅ 1:1 / 4:5 (1.91:1 landscape) | ✅ 4:5 / 1:1 / landscape | ⚠️ **No native single image** — TikTok images publish only via Photo Mode. Flag: "single image not a TikTok format → use Photo carousel". |
| **Post + video** → *Video Post* | ⚠️ **No feed video** — publishes as a Reel. Audit as **9:16 Reel**, not 16:9. | ✅ **Feed Video Post** — the one place 16:9 landscape is native (also 4:5/1:1). | ⚠️ All TikTok video is **9:16**, not 16:9. Audit as 9:16, not landscape. |
| **Reel** → *Reel* | ✅ 9:16, ≤90s | ✅ 9:16, 3–90s | ✅ 9:16 (TikTok's standard video), ≤10 min |
| **Carousel** → *Carousel* | ✅ up to **20** slides, first slide sets ratio (4:5 best), mixed media OK | ✅ up to **~10** cards | ⚠️ **Photo Mode**: **4–35** images, 9:16 (also 1:1/4:5), **500 MB** total — distinct caps from IG. |

**Consequence for the specs table:** the current `rules.plan_visual` rule "video & not Reel → 16:9"
is only correct for **Facebook**. For Instagram and TikTok a non-Reel video should be 9:16. The audit
must not inherit that flat assumption — it keys aspect expectations on **platform × type**. *(This
also surfaces a latent generation-side quirk in `plan_visual` for IG/TikTok video posts — noted as an
observation; fixing generation is out of scope for the audit feature.)*

---

## Gaps vs. the current 4-type taxonomy

| Missing / mis-specified | Present on | Recommendation |
|---|---|---|
| **Stories** (9:16 ephemeral; FB caps 100 MB/file, 60s/segment) | IG, FB, TikTok | **Out of scope for now** — not in the calendar's `Format` enum. Add a `Story` type + spec *later* only if the team starts scheduling Stories. Record as a known limitation. |
| **TikTok Photo Mode** treated as a generic carousel | TikTok | **In scope** — the specs table gets a **TikTok-specific carousel** cell (4–35 slides, 9:16, 500 MB), separate from IG's (≤20) and FB's (≤10). |
| **Instagram "Video Post"** audited as 16:9 | IG | **In scope** — normalize IG non-Reel video to a **9:16 Reel** expectation and note it. |
| **TikTok "Video Post" / single image** | TikTok | **In scope** — TikTok video → 9:16; single image → flag "use Photo carousel". |
| **Facebook text / link-only posts** (no asset; `External Link` column exists) | FB | **In scope (light):** asset checks report **NA (no media)**; caption checks still apply. Do not treat "no asset" as a failure for these. |
| **Mixed-media carousels** (IG carousel may contain video slides) | IG | Audit each slide by its actual mime type; don't assume all slides are images. |

---

## Decision (2026-07-25): shared source of truth + fix generation

The team chose to update the taxonomy to match reality **canonically**, not just inside the audit:

- **New canonical spec module** `content_hub/social/specs.py` — a `SPECS[platform][type]` matrix
  (the 12 primary cells) plus a resolver `platform_spec(platform, format, visual_type)` and a small
  **validity/normalization map** encoding the ⚠️ cells above. Pure/deterministic, no I/O
  (same contract as `rules.py`), each entry carrying a source citation + `last_verified` date.
- **`rules.plan_visual` refactored to consume it.** It gains a `platform` parameter (today it takes
  only `visual_type`, `fmt`) and derives the target aspect ratio from `specs`, so **generation** now
  produces platform-correct shapes: IG/TikTok non-Reel video → **9:16** (Facebook feed video stays
  **16:9**, which was correct). `VisualPlan`'s shape is unchanged, so `workflow.py`/`preview.py` are
  untouched; the only call site is `calendar._build_job` ([calendar.py:348](../../../../content_hub/social/calendar.py)),
  which already has `platform` in hand.
- **The audit reads the same `specs` module** — generation and audit can never drift.
- **Per-platform carousel caps** (IG 20 / FB 10 / TikTok Photo Mode 35, 500 MB), a **no-media path**
  for FB text/link posts (asset = NA, caption still audited), and **Stories documented as out of
  scope** (not in the `Format` enum) — all live in `specs`.

The **four audited types remain the classification layer**; `specs` is the platform-aware standard
they resolve against.

### Blast radius of the generation fix (accepted, to be managed in build/test)
- Existing IG/TikTok `Post`+video assets already on Drive are **not auto-regenerated** — the
  idempotency check skips a row whose asset exists. They will be **flagged by the audit** and only
  regenerate if the Drive file is deleted (the established "delete to regenerate" signal).
- **Facebook** video posts are unaffected (16:9 preserved).
- Image posts / Reels / carousels are unaffected (their aspect ratios were already correct).
- Net: the change corrects *future* generation and makes *existing* drift visible via the audit,
  rather than silently rewriting approved assets.

---

## Sources (verified 2026-07)
- Instagram media specs & formats — HeyOrca; Buffer Instagram size guide 2026.
- TikTok Photo Mode / carousel & video specs — PostFast TikTok sizes; UseVisuals TikTok carousel specs.
- Facebook post types & specs — BrandGhost Facebook post types; HeyOrca Facebook posting specs 2026.
