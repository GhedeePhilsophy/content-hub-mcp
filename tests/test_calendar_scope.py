"""Tests for Calendar row scoping — which rows `social generate` will act on.

Pure reader logic: an in-memory workbook goes in, RowJobs come out. No Drive, no
Sheets, no model calls.

Run:  pip install -r requirements-dev.txt  &&  pytest tests/test_calendar_scope.py
"""

from __future__ import annotations

import io

import openpyxl
import pytest

from content_hub.social.calendar import SHELL_HEADERS, Calendar

DRIVE_LINK = "https://drive.google.com/file/d/1GyLe46W2XbzB4D28g6SUySQ3gxMV6owK/view"


def build_calendar(*rows: dict) -> Calendar:
    """A Calendar over a one-sheet workbook using the real shell headers. Each row dict
    is keyed by header name; unlisted columns are left blank."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(SHELL_HEADERS)
    for row in rows:
        ws.append([row.get(h) for h in SHELL_HEADERS])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Calendar(buf)


def row(**over) -> dict:
    """A Draft Instagram AI-video row — in scope unless a field is overridden away."""
    base = {"Status": "Draft", "Row ID": "08-10-IG-01", "Platform": "Instagram",
            "Format": "Post", "Visual Type": "AI text-to-video",
            "Prompt": "a candle burning in a dark room"}
    base.update(over)
    return base


def only_job(*rows: dict):
    jobs = build_calendar(*rows).read_jobs()
    assert len(jobs) == 1
    return jobs[0]


def test_baseline_row_is_in_scope():
    job = only_job(row())
    assert job.in_scope
    assert job.plan.kind == "video"


@pytest.mark.parametrize("status", ["Approved", "Awaiting Asset", "Scheduled", ""])
def test_only_draft_rows_are_generated(status):
    """Approval is a human decision in the sheet; generate never acts on a non-Draft row."""
    job = only_job(row(Status=status))
    assert not job.in_scope
    assert "not Draft" in job.skip_reason


def test_no_prompt_and_no_created_asset_is_skipped():
    job = only_job(row(Prompt=None))
    assert not job.in_scope
    assert job.skip_reason == "no prompt in Prompt column"


@pytest.mark.parametrize("visual_type,fmt", [
    ("AI text-to-video", "Post"),
    ("AI text-to-video", "Reel"),
    ("AI text-to-image", "Post"),
])
def test_created_asset_link_makes_a_promptless_row_in_scope(visual_type, fmt):
    """The row is filled by COPYING the human-picked file, so no Prompt is needed —
    the same exemption the carousel branch makes for a folder-sourced set. Regression:
    these rows used to be dropped by the no-prompt guard before the copy path ran."""
    job = only_job(row(Prompt=None, **{"Visual Type": visual_type, "Format": fmt,
                                       "Created Asset Link": DRIVE_LINK}))
    assert job.in_scope, job.skip_reason
    assert job.selected_link == DRIVE_LINK
    assert job.assets, "a selected-asset row still needs an asset to name the copy"


def test_promptless_created_asset_row_survives_the_generate_filters():
    """End-to-end on the pure side: what `generate --only video` actually keeps."""
    from content_hub.social import workflow

    job = only_job(row(Prompt=None, **{"Created Asset Link": DRIVE_LINK}))
    assert workflow._uses_selected(job)
    assert job.in_scope and job.plan.kind == "video"


def test_created_asset_link_does_not_rescue_a_non_draft_row():
    job = only_job(row(Status="Approved", Prompt=None,
                       **{"Created Asset Link": DRIVE_LINK}))
    assert not job.in_scope
    assert "not Draft" in job.skip_reason


def test_carousel_without_slides_is_still_skipped_with_a_created_asset_link():
    """The Created Asset exemption is about the Prompt only — Slides stays required."""
    job = only_job(row(Prompt=None, **{"Visual Type": "AI text-to-carousel",
                                       "Format": "Carousel",
                                       "Created Asset Link": DRIVE_LINK}))
    assert not job.in_scope
    assert "Slides" in job.skip_reason


def test_blank_row_id_rows_are_ignored():
    assert build_calendar(row(**{"Row ID": None})).read_jobs() == []
