"""Test isolation: each pytest run gets a fresh, ephemeral SQLite DB.

Without this, the suite writes to the dev `studio.db` (a persistent file), so a second
run fails on uniqueness guards (e.g. duplicate watchlist names) from the prior run's
rows. Set DATABASE_URL before any `studioapi` import so the engine binds to the temp DB.
"""

from __future__ import annotations

import os
import tempfile

_db_path = os.path.join(tempfile.mkdtemp(prefix="studio-test-"), "studio_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
# ASK-5: keep the ask-feed refresher loop out of the suite (its ticks would hit agent-engine).
os.environ["ASK_FEED_ENABLED"] = "false"
