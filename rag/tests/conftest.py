"""HI-8: search() now keeps process-wide result + embed caches. Clear them (and the existing
multi-query cache) around every test so a mocked corpus / query never leaks into the next test."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_search_caches():
    from rag import search

    def _reset():
        search._result_cache.clear()
        search._embed_cache.clear()
        search._mq_cache.clear()

    _reset()
    yield
    _reset()
