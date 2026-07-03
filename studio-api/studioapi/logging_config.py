"""Logging + request tracing for the studio-api service.

A single LOG_LEVEL (read straight from the environment, so it works regardless of the
pydantic ``env_prefix``) drives verbosity for the app logger, uvicorn, and the request
middleware. Set ``LOG_LEVEL=DEBUG`` for full traces (file:line + funcName, raw model
payloads, per-retry backoff). Without this, ``studioapi.*`` loggers have no level/handler
so INFO/DEBUG never reach ``docker logs`` and best-effort except-blocks go silent.

Noise control so DEBUG stays readable:
  * a SafeFormatter never lets a bad ``logging`` call crash-spam ("--- Logging error ---");
  * chatty third-party libraries (httpcore/urllib3/matplotlib/asyncio/…) are floored;
  * records originating inside pykrx are dropped — it mis-calls ``logging.info(tuple, dict)``
    on the root logger, which would otherwise raise a TypeError on every KRX miss.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid

from studioapi.config import settings

_PKG = "studioapi"
_BASE_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DEBUG_FMT = "%(asctime)s %(levelname)-7s %(name)s [%(filename)s:%(lineno)d %(funcName)s]: %(message)s"

# Libraries whose DEBUG/INFO output is pure noise for app debugging → floor them here even
# when the app runs at DEBUG. (name → minimum level.)
_LIB_FLOORS = {
    "httpcore": logging.WARNING,   # per-byte connect/send/receive trace spam
    "urllib3": logging.WARNING,
    "httpx": logging.INFO,         # keep the one-line "HTTP Request:" log, drop its DEBUG
    "matplotlib": logging.WARNING,
    "fontTools": logging.WARNING,
    "PIL": logging.WARNING,
    "asyncio": logging.INFO,       # selector spam at DEBUG
    "pykrx": logging.WARNING,
}

logger = logging.getLogger(_PKG)


class _SafeFormatter(logging.Formatter):
    """A formatter that can never crash the program: if a record's ``msg % args`` is malformed
    (some libraries mis-call logging), fall back to a repr instead of raising a logging error."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            return super().format(record)
        except Exception:  # noqa: BLE001 — logging must not raise
            try:
                return f"{record.levelname} {record.name}: {record.msg!r} args={record.args!r}"
            except Exception:  # noqa: BLE001
                return "<unformattable log record>"


class _DropNoise(logging.Filter):
    """Drop records that come from inside pykrx — its util wrapper calls ``logging.info(args,
    kwargs)`` on the ROOT logger (a tuple as the message), which crashes formatting on every
    KRX request miss. Filtering by source path kills it before emit, without touching pykrx."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return "/pykrx/" not in (record.pathname or "")


def _resolve_level() -> int:
    raw = os.environ.get("LOG_LEVEL") or getattr(settings, "log_level", "INFO")
    return getattr(logging, str(raw).upper(), logging.INFO)


def setup_logging() -> None:
    """Install one stdout handler on the root logger at LOG_LEVEL; app + uvicorn follow it."""
    level = _resolve_level()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_SafeFormatter(_DEBUG_FMT if level <= logging.DEBUG else _BASE_FMT, "%H:%M:%S"))
    handler.addFilter(_DropNoise())
    handler.setLevel(level)
    handler._vg = True  # type: ignore[attr-defined]  # mark so we install exactly once

    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, "_vg", False)]
    root.addHandler(handler)
    root.setLevel(level)

    # app + uvicorn loggers share the root handler at the same level (no duplicate lines).
    for name in (_PKG, "uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []          # use the root handler only (no duplicate lines)
        lg.setLevel(level)
        lg.propagate = True
    # the plain access line is replaced by our richer request middleware → quiet it.
    acc = logging.getLogger("uvicorn.access")
    acc.handlers = []
    acc.setLevel(logging.WARNING)
    acc.propagate = True
    # floor noisy libraries so a global DEBUG stays readable.
    for name, floor in _LIB_FLOORS.items():
        logging.getLogger(name).setLevel(max(level, floor))
    logger.info("logging configured: level=%s service=%s", logging.getLevelName(level), _PKG)


def install_request_logging(app) -> None:
    """Trace every HTTP request: a → line on entry, a ← line with status + duration on exit, a
    short request id to correlate the two, and a full traceback on any unhandled error. At DEBUG
    the entry line also carries the query string."""

    @app.middleware("http")
    async def _trace(request, call_next):  # noqa: ANN001
        rid = uuid.uuid4().hex[:8]
        path = request.url.path
        qs = f"?{request.url.query}" if (request.url.query and logger.isEnabledFor(logging.DEBUG)) else ""
        logger.info("→ %s %s%s rid=%s", request.method, path, qs, rid)
        t0 = time.perf_counter()
        try:
            resp = await call_next(request)
        except Exception:
            dt = (time.perf_counter() - t0) * 1000
            logger.exception("✗ %s %s rid=%s after %.1fms — unhandled", request.method, path, rid, dt)
            raise
        dt = (time.perf_counter() - t0) * 1000
        lvl = logging.WARNING if resp.status_code >= 400 else logging.INFO
        logger.log(lvl, "← %s %s %d %.1fms rid=%s", request.method, path, resp.status_code, dt, rid)
        resp.headers["X-Request-ID"] = rid
        return resp
