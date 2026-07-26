"""Property + table tests for the canonical specs and the plan_visual refactor (Unit A).

PBT extension = Partial: these cover the PURE functions only (specs resolution, aspect
targeting, classification, and plan_visual's aspect output). I/O glue is validated via the
CLI dry-run/mock harness, not here.

Run:  pip install -r requirements-dev.txt  &&  pytest tests/test_specs.py
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from content_hub.social import rules, specs

KNOWN_PLATFORMS = ["Instagram", "Facebook", "Tiktok"]
# include odd spellings + junk to exercise norm_platform's fallback
PLATFORM_STRATEGY = st.one_of(
    st.sampled_from(KNOWN_PLATFORMS + ["instagram", "IG", "TikTok", "  facebook ", "tik tok"]),
    st.none(),
    st.text(max_size=8),
)
KIND_STRATEGY = st.sampled_from(["image", "video", "carousel"])


# --- target_aspect: the core generation contract ---------------------------
@given(platform=PLATFORM_STRATEGY, kind=KIND_STRATEGY, is_reel=st.booleans())
def test_target_aspect_is_always_an_accepted_aspect(platform, kind, is_reel):
    """Whatever generation targets must be a ratio the audit would PASS."""
    ar = specs.target_aspect(platform, kind, is_reel)
    post_type = specs.kind_to_post_type(kind, is_reel)
    spec = specs.media_spec(platform, post_type)
    assert ar == spec.target_aspect
    assert ar in spec.accepted_aspects


@given(is_reel=st.booleans())
@pytest.mark.parametrize("platform", ["Instagram", "Tiktok", "instagram", "tik tok"])
def test_ig_and_tiktok_video_is_never_landscape(platform, is_reel):
    """The headline fix: Instagram/TikTok video (Reel or feed Post) is 9:16, never 16:9."""
    assert specs.target_aspect(platform, "video", is_reel) == "9:16"


@pytest.mark.parametrize("is_reel", [True, False])
def test_facebook_feed_video_stays_landscape_but_reel_is_vertical(is_reel):
    """Facebook is the one platform that keeps 16:9 — but only for a non-Reel feed video."""
    assert specs.target_aspect("Facebook", "video", is_reel) == ("9:16" if is_reel else "16:9")


@given(platform=PLATFORM_STRATEGY, is_reel=st.booleans())
def test_images_reels_carousels_unchanged_across_platforms(platform, is_reel):
    """Only video_post shape changed; image=1:1, reel=9:16, carousel=4:5 everywhere."""
    assert specs.target_aspect(platform, "image", is_reel) == "1:1"
    assert specs.target_aspect(platform, "carousel", is_reel) == "4:5"
    assert specs.target_aspect(platform, "video", True) == "9:16"  # a reel is always vertical


def test_unknown_platform_preserves_legacy_16x9_video():
    """A blank/unknown Platform must reproduce the old flat rule (non-Reel video -> 16:9)."""
    assert specs.target_aspect("", "video", False) == "16:9"
    assert specs.target_aspect("Threads", "video", False) == "16:9"
    assert specs.target_aspect(None, "video", False) == "16:9"


# --- classify: validity / normalization ------------------------------------
def test_instagram_feed_video_normalizes_to_reel_with_a_note():
    post_type, issues = specs.classify("Instagram", "Post", "video", is_reel=False)
    assert post_type == specs.REEL
    assert issues and "Reel" in issues[0]


def test_tiktok_single_image_is_flagged():
    post_type, issues = specs.classify("Tiktok", "Post", "image", is_reel=False)
    assert post_type == specs.IMAGE_POST
    assert issues and "Photo Mode" in issues[0]


def test_valid_combos_have_no_issues():
    assert specs.classify("Instagram", "Post", "image")[1] == []
    assert specs.classify("Facebook", "Post", "video")[1] == []
    assert specs.classify("Tiktok", "Reel", "video")[1] == []
    assert specs.classify("Instagram", "Carousel", "carousel")[1] == []


# --- plan_visual: end-to-end aspect output ---------------------------------
def test_plan_visual_ig_post_video_now_vertical():
    plan = rules.plan_visual(rules.VT_VIDEO, "Post", "Instagram")
    assert plan.kind == "video" and plan.aspect_ratio == "9:16" and plan.generate


def test_plan_visual_facebook_post_video_stays_landscape():
    plan = rules.plan_visual(rules.VT_VIDEO, "Post", "Facebook")
    assert plan.aspect_ratio == "16:9"


def test_plan_visual_reel_vertical_everywhere():
    for p in KNOWN_PLATFORMS:
        assert rules.plan_visual(rules.VT_VIDEO, "Reel", p).aspect_ratio == "9:16"


def test_plan_visual_image_and_carousel_unchanged():
    for p in KNOWN_PLATFORMS + [None, ""]:
        assert rules.plan_visual(rules.VT_IMAGE, "Post", p).aspect_ratio == "1:1"
        assert rules.plan_visual(rules.VT_CAROUSEL, "Carousel", p).aspect_ratio == "4:5"


def test_plan_visual_backward_compatible_without_platform():
    """The new platform arg is optional — old 2-arg calls still work (legacy generic)."""
    assert rules.plan_visual(rules.VT_VIDEO, "Post").aspect_ratio == "16:9"
    assert rules.plan_visual(rules.VT_VIDEO, "Reel").aspect_ratio == "9:16"
    assert rules.plan_visual(rules.VT_IMAGE, "Post").aspect_ratio == "1:1"


def test_recorded_video_unchanged():
    plan = rules.plan_visual(rules.VT_RECORDED, "Reel", "Instagram")
    assert plan.recorded and not plan.generate and plan.aspect_ratio == "9:16"


def test_caption_specs_present_for_each_platform():
    assert specs.caption_spec("Instagram").caption_max == 2200
    assert specs.caption_spec("Instagram").hashtag_max == 30
    assert specs.caption_spec("Tiktok").caption_max == 4000
    assert specs.caption_spec("Facebook").hashtag_max is None
