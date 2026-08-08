"""Property + unit tests for the audit's pure logic (Unit B + Tier 1/2 follow-up).

PBT = Partial: covers the pure measurement/verdict helpers only. The Drive/Sheets I/O is
validated via the CLI dry-run/mock harness, not here.

Run:  pytest tests/test_audit.py
"""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import given, strategies as st

from content_hub.social import audit, specs


# --- aspect math -----------------------------------------------------------
@given(n=st.integers(min_value=64, max_value=8000))
def test_square_dims_are_1to1(n):
    assert audit.measured_aspect_label(n, n) == "1:1"
    assert audit.aspect_matches(n, n, ("1:1",))


def test_known_generation_sizes_label_correctly():
    assert audit.measured_aspect_label(1024, 1280) == "4:5"
    assert audit.measured_aspect_label(1024, 1792) == "9:16"
    assert audit.measured_aspect_label(1792, 1024) == "16:9"
    assert audit.measured_aspect_label(1080, 1350) == "4:5"


def test_aspect_matches_respects_tolerance_and_rejects_mismatch():
    assert not audit.aspect_matches(1792, 1024, ("9:16",))
    assert audit.aspect_matches(1080, 1920, ("9:16",))
    assert audit.aspect_matches(1024, 1280, ("1:1", "4:5"))


@given(w=st.integers(min_value=64, max_value=8000), h=st.integers(min_value=64, max_value=8000))
def test_measured_label_never_crashes_and_matches_self(w, h):
    label = audit.measured_aspect_label(w, h)
    assert isinstance(label, str) and label
    if label in audit.KNOWN_RATIOS:
        assert audit.aspect_matches(w, h, (label,))


def test_parse_aspect():
    assert abs(audit.parse_aspect("9:16") - 0.5625) < 1e-9
    assert abs(audit.parse_aspect("1.91:1") - 1.91) < 1e-9
    assert audit.parse_aspect("bad") is None
    assert audit.parse_aspect("1:0") is None


# --- text helpers ----------------------------------------------------------
def test_count_hashtags_across_fields():
    assert audit.count_hashtags("#a #b hello #c") == 3
    assert audit.count_hashtags("no tags here") == 0
    assert audit.count_hashtags("#one", "#two #three") == 3
    assert audit.count_hashtags("C# is not a tag; # alone no") == 0


def test_find_urls():
    assert audit.find_urls("see https://ghedee.com/x now") == ["https://ghedee.com/x"]
    assert audit.find_urls("visit www.ghedee.com") == ["www.ghedee.com"]
    assert audit.find_urls("no link here") == []


def test_duplicate_hashtags_case_insensitive():
    assert audit.duplicate_hashtags("#Law #law #philosophy") == ["#law"]
    assert audit.duplicate_hashtags("#a #b", "#c #a") == ["#a"]
    assert audit.duplicate_hashtags("#a #b #c") == []


# --- caption checks --------------------------------------------------------
def _row():
    return audit.RowAudit("R1", "Instagram", specs.IMAGE_POST, "Post", "Draft")


def test_caption_over_cap_fails():
    r = _row()
    audit.audit_caption(r, "x" * 2201, "", specs.caption_spec("Instagram"), "Instagram")
    r.finalize()
    assert any(c.name == "caption_length" and c.verdict == "FAIL" for c in r.checks)
    assert r.overall == "FAIL"


def test_caption_within_cap_passes_and_records_fold():
    r = _row()
    audit.audit_caption(r, "hello world", "", specs.caption_spec("Instagram"), "Instagram")
    assert any(c.name == "caption_length" and c.verdict == "PASS" for c in r.checks)
    assert r.measured["fold_preview"] == "hello world"


def test_empty_caption_warns():
    r = _row()
    audit.audit_caption(r, "   ", "", specs.caption_spec("Instagram"), "Instagram")
    assert any(c.name == "caption_length" and c.verdict == "WARN" for c in r.checks)


def test_too_many_hashtags_warns_on_instagram():
    r = _row()
    tags = " ".join(f"#t{i}" for i in range(31))
    audit.audit_caption(r, "caption", tags, specs.caption_spec("Instagram"), "Instagram")
    assert any(c.name == "hashtags" and c.verdict == "WARN" for c in r.checks)


def test_facebook_has_no_hashtag_cap():
    r = audit.RowAudit("R2", "Facebook", specs.IMAGE_POST, "Post", "Draft")
    tags = " ".join(f"#t{i}" for i in range(50))
    audit.audit_caption(r, "caption", tags, specs.caption_spec("Facebook"), "Facebook")
    assert not any(c.name == "hashtags" and c.verdict == "WARN" for c in r.checks)


def test_link_in_caption_warns_on_instagram_not_facebook():
    ig = _row()
    audit.audit_caption(ig, "read more at https://ghedee.com", "",
                        specs.caption_spec("Instagram"), "Instagram")
    assert any(c.name == "caption_links" for c in ig.checks)
    fb = audit.RowAudit("R3", "Facebook", specs.IMAGE_POST, "Post", "Draft")
    audit.audit_caption(fb, "read more at https://ghedee.com", "",
                        specs.caption_spec("Facebook"), "Facebook")
    assert not any(c.name == "caption_links" for c in fb.checks)


def test_hashtags_in_caption_body_warns_on_instagram():
    r = _row()
    audit.audit_caption(r, "great post #law #truth", "", specs.caption_spec("Instagram"),
                        "Instagram")
    assert any(c.name == "hashtag_placement" for c in r.checks)


def test_duplicate_hashtags_flagged():
    r = _row()
    audit.audit_caption(r, "#law and #Law again", "", specs.caption_spec("Instagram"),
                        "Instagram")
    assert any(c.name == "hashtag_dupes" for c in r.checks)


# --- duplicate assets ------------------------------------------------------
def test_shared_asset_across_platforms_is_allowed():
    """The same clip on TikTok and on an Instagram Reel is legitimate reuse."""
    assert audit.duplicate_asset_conflicts("TikTok", "video", "Instagram", "video") == ()


def test_shared_asset_on_same_platform_flagged():
    assert "same_platform" in audit.duplicate_asset_conflicts(
        "Instagram", "video", "Instagram", "video")


def test_shared_asset_platform_aliases_count_as_the_same_platform():
    assert "same_platform" in audit.duplicate_asset_conflicts(
        "IG", "image", "Instagram Reel", "image")


def test_shared_asset_with_mismatched_kind_flagged():
    assert "kind_mismatch" in audit.duplicate_asset_conflicts(
        "TikTok", "video", "Facebook", "image")


def test_shared_asset_can_be_flagged_for_both_reasons():
    assert set(audit.duplicate_asset_conflicts("Instagram", "video", "Instagram", "image")) \
        == {"same_platform", "kind_mismatch"}


# --- readiness -------------------------------------------------------------
@dataclass
class _Plan:
    kind: str = "image"
    recorded: bool = False
    reason: str = ""
    aspect_ratio: str = "1:1"
    generate: bool = True


@dataclass
class _Job:
    status: str = "Approved"
    existing_link: str = "https://drive.google.com/file/d/x/view"
    selected_link: str | None = None
    caption: str = "a caption"
    platform: str = "Instagram"
    fmt: str = "Post"
    date: str = "2026-08-01"
    plan: _Plan = None

    def __post_init__(self):
        if self.plan is None:
            self.plan = _Plan()


def test_readiness_approved_without_asset_fails():
    r = _row()
    audit.audit_readiness(r, _Job(existing_link=""), time_val="9:00 AM")
    assert any(c.name == "readiness" and c.verdict == "FAIL" for c in r.checks)


def test_readiness_failed_asset_fails():
    r = _row()
    audit.audit_readiness(r, _Job(existing_link="Failed"), time_val="9:00 AM")
    assert any(c.verdict == "FAIL" for c in r.checks)


def test_readiness_missing_time_warns():
    r = _row()
    audit.audit_readiness(r, _Job(), time_val="")
    assert any(c.name == "readiness_fields" and "Time" in c.detail for c in r.checks)


def test_readiness_skips_drafts():
    r = _row()
    audit.audit_readiness(r, _Job(status="Draft", existing_link="", caption=""),
                          time_val="")
    assert r.checks == []


# --- media checks + verdict aggregation ------------------------------------
def test_check_media_flags_wrong_aspect_and_oversize():
    r = _row()
    spec = specs.media_spec("Tiktok", specs.VIDEO_POST)  # 9:16 only, 500 MB
    audit._check_media(r, 1920, 1080, 900.0, spec)
    r.finalize()
    verdicts = {c.name: c.verdict for c in r.checks}
    assert verdicts["aspect"] == "FAIL"
    assert verdicts["file_size"] == "FAIL"
    assert r.overall == "FAIL"


def test_check_media_all_pass():
    r = _row()
    spec = specs.media_spec("Instagram", specs.IMAGE_POST)
    audit._check_media(r, 1080, 1350, 2.1, spec)
    r.finalize()
    assert r.overall == "PASS"


def test_video_duration_over_max_fails_under_min_warns():
    spec = specs.media_spec("Instagram", specs.REEL)  # 3–180s
    over = _row()
    audit._check_media(over, 1080, 1920, 5.0, spec, duration=200.0)
    assert any(c.name == "duration" and c.verdict == "FAIL" for c in over.checks)
    under = _row()
    audit._check_media(under, 1080, 1920, 5.0, spec, duration=1.5)
    assert any(c.name == "duration" and c.verdict == "WARN" for c in under.checks)
    ok = _row()
    audit._check_media(ok, 1080, 1920, 5.0, spec, duration=30.0)
    assert any(c.name == "duration" and c.verdict == "PASS" for c in ok.checks)


def test_finalize_picks_worst_verdict_fail_beats_warn():
    r = _row()
    r.add("a", "PASS", "")
    r.add("b", "WARN", "")
    r.finalize()
    assert r.overall == "WARN"
    r.add("c", "FAIL", "")
    r.finalize()
    assert r.overall == "FAIL"  # FAIL beats WARN


def test_status_word_and_note_text():
    r = _row()
    r.add("caption_length", "PASS", "ok")
    r.finalize()
    assert r.status_word() == "PASS" and r.note_text() == ""
    r.add("aspect", "FAIL", "16:9 not 9:16")
    r.add("resolution", "WARN", "short edge low")
    r.finalize()
    assert r.status_word() == "FAIL"
    note = r.note_text()
    assert "FAIL:" in note and "16:9 not 9:16" in note and "WARN:" in note


def test_na_row_has_blank_status():
    r = _row()
    r.add("asset", "NA", "no asset")
    r.finalize()
    assert r.overall == "NA"
    assert r.status_word() == "" and r.note_text() == ""
