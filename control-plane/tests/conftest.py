"""Test isolation: each pytest run gets a fresh, ephemeral SQLite DB.

Without this the suite writes to the dev `controlplane.db` (a persistent file), so results depend on
whatever a previous run — or a previously deployed schema — left behind. That is exactly wrong for the
PROV-1 tests, whose whole subject is DB constraints: a stale file missing `projects.owner_ref` or the
activation unique index would make them pass or fail for reasons that have nothing to do with the code
under test. Set DATABASE_URL before any `controlplane` import so the engine binds to the temp DB.
"""

from __future__ import annotations

import os
import tempfile

_db_path = os.path.join(tempfile.mkdtemp(prefix="cp-test-"), "controlplane_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
