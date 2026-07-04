"""Content Hub — MCP server backing the Ghedee Content Hub Cowork workflows.

Three content types are planned (Blog Posts, Social Media Calendar, Emails); the
common thread across all of them is: generate AI images/video, push files to a
specific Google Drive folder, and pull files back for Cowork to ingest. That
shared thread lives in ``core``; each content type is its own package on top:

    core/           config, media (generate), drive (push/pull)  — content-agnostic
    social/         Social Calendar workflow (rules, calendar, workflow)  [phase 1]
    (blog/, email/  future siblings, same shape)
    ../server.py    thin MCP tools that wire a workflow to the core

Every operation supports three modes so a run can be rehearsed before it spends
anything: ``dry-run`` (plan only), ``mock`` (real pipeline, placeholder files,
safe destination), and ``live``.
"""

__all__ = ["core", "social"]
