"""Property + unit tests for the preview simulators' pure logic.

PBT = Partial: covers the deterministic engagement derivation and the relative-timestamp
helper only. The DOM/rendering side is browser code, validated by building the page (see
`social preview <id> --no-publish`), not here.

The invariants below are the ones named in the functional design: determinism, coherence,
in-band, totality.

Run:  pytest tests/test_preview_engagement.py
"""

from __future__ import annotations

import subprocess
import sys

from hypothesis import given, strategies as st

from content_hub.social import preview

PLATFORMS = ["Instagram", "Facebook", "Tiktok", "", "something-unrecognised"]

# any row id the sheet could plausibly hold, including junk
row_ids = st.text(min_size=0, max_size=40)


# --- determinism -----------------------------------------------------------
@given(rid=row_ids, is_reel=st.booleans(), platform=st.sampled_from(PLATFORMS))
def test_engagement_is_deterministic(rid, platform, is_reel):
    assert preview.engagement(rid, platform, is_reel) == \
           preview.engagement(rid, platform, is_reel)


def test_engagement_is_stable_across_processes():
    """The whole point of FNV-1a over hash(): counts must not change between builds.

    hash() is salted per interpreter, so this test is what would have caught that bug.
    """
    code = ("from content_hub.social.preview import engagement;"
            "print(engagement('IG-014','Instagram',False)['likes'])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True).stdout.strip()
    assert int(out) == preview.engagement("IG-014", "Instagram", False)["likes"]


def test_distinct_rows_generally_differ():
    """Not an invariant of a hash, but a smoke test that the feed isn't uniform."""
    likes = {preview.engagement(f"IG-{i:03d}", "Instagram")["likes"] for i in range(40)}
    assert len(likes) > 20


# --- coherence -------------------------------------------------------------
@given(rid=row_ids, is_reel=st.booleans(), platform=st.sampled_from(PLATFORMS))
def test_engagement_is_coherent(rid, platform, is_reel):
    e = preview.engagement(rid, platform, is_reel)
    assert e["comments"] <= e["likes"], "more comments than likes reads as broken"
    assert e["shares"] <= e["likes"]
    assert e["saves"] <= e["likes"]
    if "views" in e:
        assert e["likes"] <= e["views"], "more likes than views is impossible"
    assert all(isinstance(v, int) and v >= 0 for v in e.values())


# --- in-band ---------------------------------------------------------------
@given(rid=row_ids, platform=st.sampled_from(PLATFORMS))
def test_still_post_likes_in_band(rid, platform):
    key = preview._platform_key(platform)
    if key == "tiktok":            # tiktok is always treated as a clip -> views band
        return
    lo, hi = preview._ENGAGE_LIKES[key]
    assert lo <= preview.engagement(rid, platform, False)["likes"] <= hi


@given(rid=row_ids)
def test_reel_views_in_band(rid):
    lo, hi = preview._ENGAGE_VIEWS
    assert lo <= preview.engagement(rid, "Instagram", True)["views"] <= hi


def test_video_surfaces_report_views_and_stills_do_not():
    assert "views" in preview.engagement("R-1", "Instagram", True)
    assert "views" in preview.engagement("T-1", "Tiktok", False)     # all TikTok is video
    assert "views" not in preview.engagement("I-1", "Instagram", False)
    assert "views" not in preview.engagement("F-1", "Facebook", False)


# --- totality --------------------------------------------------------------
@given(rid=row_ids, platform=st.text(max_size=20), is_reel=st.booleans())
def test_engagement_never_raises(rid, platform, is_reel):
    preview.engagement(rid, platform, is_reel)


@given(s=st.text(max_size=60))
def test_fnv_never_raises_and_is_32_bit(s):
    h = preview._fnv1a32(s)
    assert 0 <= h <= 0xFFFFFFFF


def test_band_handles_degenerate_range():
    assert preview._band("x", "m", 5, 5) == 5
    assert preview._band("x", "m", 9, 3) == 9      # hi < lo -> lo, no crash


# --- relative timestamps ---------------------------------------------------
def test_rel_time_units():
    n = "2026-07-26"
    assert preview._rel_time("2026-07-26", n).endswith("h")   # same day -> hours
    assert preview._rel_time("2026-07-24", n) == "2d"
    assert preview._rel_time("2026-07-12", n) == "2w"
    assert preview._rel_time("2026-05-26", n) == "2mo"


@given(bad=st.text(max_size=12))
def test_rel_time_is_total(bad):
    preview._rel_time(bad, "2026-07-26")
    preview._rel_time("2026-07-26", bad)


def test_rel_time_blank_for_undated():
    assert preview._rel_time("", "2026-07-26") == ""
    assert preview._rel_time("2026-07-26", "") == ""


def test_rel_time_never_reports_an_older_post_as_newer():
    """Ages must not invert as the unit changes (h -> d -> w -> mo)."""
    order = {"h": 0, "d": 1, "w": 2, "mo": 3}

    def rank(label):
        unit = "mo" if label.endswith("mo") else label[-1]
        return (order[unit], int(label[:-len(unit)]))

    newest = "2026-07-26"
    dates = ["2026-07-26", "2026-07-25", "2026-07-20", "2026-07-05", "2026-05-01"]
    ranks = [rank(preview._rel_time(d, newest)) for d in dates]
    assert ranks == sorted(ranks), f"age labels inverted: {ranks}"
