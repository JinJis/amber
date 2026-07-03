"""Shared HTTP helper for channel adapters: POST with one backoff retry."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("studioapi.channels")


def post_json(url: str, body: dict, *, retries: int = 1, timeout: float = 8.0) -> bool:
    """POST JSON, returning True on 2xx. Retries once with a short backoff on error/non-2xx."""
    for attempt in range(retries + 1):
        try:
            r = httpx.post(url, json=body, timeout=timeout)
            if 200 <= r.status_code < 300:
                return True
            log.warning("channel POST %s -> %s", url, r.status_code)
        except httpx.HTTPError as exc:
            log.warning("channel POST error (%s/%s): %s", attempt + 1, retries + 1, exc)
        if attempt < retries:
            time.sleep(0.4 * (attempt + 1))
    return False
