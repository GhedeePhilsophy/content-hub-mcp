"""core — the content-agnostic common thread shared by every Content Hub workflow.

  config   env / Google credentials / paths / brand + model defaults
  media    generate images + video from asset dicts (the AI-image primitive)
  drive    push files to a Drive folder / pull the latest / existence-check

Blog, social, and email workflows all build on these; nothing here knows about
any specific content type.
"""

__all__ = ["config", "media", "drive"]
