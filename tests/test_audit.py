"""Property + unit tests for the audit's pure logic (Unit B).

PBT = Partial: covers the pure measurement/verdict helpers only. The Drive/Sheets I/O is
validated via the CLI dry-run/mock harness, not here.

Run:  pytest tests/test_audit.py
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from content_hub.social import audit, specs


# --- aspect math -----------------------------------------------------------
@given(n=st.integers(min_value=64, max_value=8000))
def test_square_dims_are_1to1(n):
    assert audit.measured_aspect_label(n, n) == "1:1"
    assert audit.aspect_matches(n, n, ("1:1",))


def test_known_generation_sizes_label_correctly():
    # the exact sizes gpt-image-2 renders (core.media.IMAGE_SIZES)
    assert audit.measured_aspect_label(1024, 1280) == "4:5"
    assert audit.measured_aspect_label(1024, 1792) == "9:16"
    assert audit.measured_aspect_label(1792, 1024) == "16:9"
    assert audit.measured_aspect_label(1080, 1350) == "4:5"


def test_aspect_matches_respects_tolerance_and_rejects_mismatch():
    # a 16:9 asset must NOT satisfy a 9:16-only expectation (the TikTok/IG video case)
    assert not audit.aspect_matches(1792, 1024, ("9:16",))
    assert audit.aspect_matches(1080, 1920, ("9:16",))
    # 4:5 accepted where 1:1 or 4:5 allowed
    assert audit.aspect_matches(1024, 1280, ("1:1", "4:5"))


@given(w=st.integers(min_value=64, max_value=8000), h=st.integers(min_value=64, max_value=8000))
def test_measured_label_never_crashes_and_matches_self(w, h):
    label = audit.measured_aspect_label(w, h)
    assert isinstance(label, str) and label
    # whatever label it reports, the dims should match that same ratio within tolerance
    if label in audit.KNOWN_RATIOS:
        assert audit.aspect_matches(w, h, (label,))


def test_parse_aspect():
    assert abs(audit.parse_aspect("9:16") - 0.5625) < 1e-9
    assert abs(audit.parse_aspect("1.91:1") - 1.91) < 1e-9
    assert audit.parse_aspect("bad") is None
    assert audit.parse_aspect("1:0") is None


# --- hashtags --------------------------------------------------------------
def test_count_hashtags_across_fields():
    assert audit.count_hashtags("#a #b hello #c") == 3
    assert audit.count_hashtags("no tags here") == 0
    assert audit.count_hashtags("#one", "#two #three") == 3
    # a bare '#' or mid-word '#' is not a tag
    assert audit.count_hashtags("C# is not a tag; # alone no") == 0


# --- caption checks --------------------------------------------------------
def _row():
    return audit.RowAudit("R1", "Instagram", specs.IMAGE_POST, "Post", "Draft")


def test_caption_over_cap_fails():
    r = _row()
    audit.audit_caption(r, "x" * 2201, "", specs.caption_spec("Instagram"))
    r.finalize()
    assert any(c.name == "caption_length" and c.verdict == "FAIL" for c in r.checks)
    assert r.overall == "FAIL"


def test_caption_within_cap_passes_and_records_fold():
    r = _row()
    audit.audit_caption(r, "hello world", "", specs.caption_spec("Instagram"))
    assert any(c.name == "caption_length" and c.verdict == "PASS" for c in r.checks)
    assert r.measured["fold_preview"] == "hello world"


def test_empty_caption_warns():
    r = _row()
    audit.audit_caption(r, "   ", "", specs.caption_spec("Instagram"))
    assert any(c.name == "caption_length" and c.verdict == "WARN" for c in r.checks)


def test_too_many_hashtags_warns_on_instagram():
    r = _row()
    tags = " ".join(f"#t{i}" for i in range(31))
    audit.audit_caption(r, "caption", tags, specs.caption_spec("Instagram"))
    assert any(c.name == "hashtags" and c.verdict == "WARN" for c in r.checks)


def test_facebook_has_no_hashtag_cap():
    r = audit.RowAudit("R2", "Facebook", specs.IMAGE_POST, "Post", "Draft")
    tags = " ".join(f"#t{i}" for i in range(50))
    audit.audit_caption(r, "caption", tags, specs.caption_spec("Facebook"))
    assert not any(c.name == "hashtags" and c.verdict == "WARN" for c in r.checks)


# --- media checks + verdict aggregation ------------------------------------
def test_check_media_flags_wrong_aspect_and_oversize():
    r = _row()
    spec = specs.media_spec("Tiktok", specs.VIDEO_POST)  # 9:16 only, 500 MB
    audit._check_media(r, 1920, 1080, 900.0, spec)  # 16:9 + oversize
    r.finalize()
    verdicts = {c.name: c.verdict for c in r.checks}
    assert verdicts["aspect"] == "FAIL"
    assert verdicts["file_size"] == "FAIL"
    assert r.overall == "FAIL"


def test_check_media_all_pass():
    r = _row()
    spec = specs.media_spec("Instagram", specs.IMAGE_POST)  # 1:1/4:5/1.91:1, 8 MB
    audit._check_media(r, 1080, 1350, 2.1, spec)  # 4:5, fine
    r.finalize()
    assert r.overall == "PASS"


def test_finalize_picks_worst_verdict():
    r = _row()
    r.add("a", "PASS", "")
    r.add("b", "WARN", "")
    r.add("c", "PASS", "")
    r.finalize()
    assert r.overall == "WARN"
    r.add("d", "FAIL", "")
    r.finalize()
    assert r.overall == "FAIL"
