"""FastAPI application entrypoint.

A financial datasets API covering the US and Korean markets. Select the
market with the ``market`` query parameter (US default, KR for KOSPI/KOSDAQ).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.errors import NOT_IMPLEMENTED_TAG, register_error_handlers
from app.logging_config import install_request_logging, setup_logging
from app.routers import (  # noqa: I001
    backtest,
    fmp,
    kis,
    admin,
    catalog,
    company,
    corporate_actions,
    earnings,
    evidence,
    filings,
    financials,
    funds,
    gurus,
    insider,
    institutional,
    macro,
    market,
    metrics,
    news,
    prices,
    scaffold,
    search,
    technical,
    valuation,
)
from app.queue import close as close_queue, open_and_migrate
from app.store.db import init_db


setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # Open the Procrastinate connector + apply its schema (idempotent), so manual admin runs can
    # defer jobs and the admin Queue console can read app.job_manager. The `worker` compose service
    # is a separate process that actually runs the periodic sweeps + processes the jobs.
    await open_and_migrate()
    try:
        yield
    finally:
        await close_queue()

app = FastAPI(
    title="ValueGraph Datasets API (US + Korea)",
    version="0.1.0",
    lifespan=lifespan,
    description=(
        "Financial datasets API covering the US market and extended to "
        "the Korean equity market (KOSPI/KOSDAQ). Use the `market` query parameter "
        "(`US` default, `KR`). **Not investment advice.**\n\n"
        "**Implementation status:** endpoints grouped under the "
        f"**{NOT_IMPLEMENTED_TAG}** tag (at the bottom) are not yet backed by real "
        "data and return HTTP 501. Everything in the other groups is implemented and "
        "returns real data (some endpoints need an upstream API key — see the README)."
    ),
    openapi_tags=[
        {
            "name": NOT_IMPLEMENTED_TAG,
            "description": (
                "These endpoints are defined in the API surface "
                "but are **not implemented yet** — every one returns **HTTP 501**. "
                "Use this section to see at a glance what is not testable yet."
            ),
        }
    ],
)

register_error_handlers(app)
install_request_logging(app)

for module in (
    company, prices, financials, filings, macro, metrics,
    news, earnings, insider, institutional, funds, gurus, corporate_actions, technical,
    market, search, evidence, catalog, admin, scaffold, valuation, backtest, fmp, kis,
):
    app.include_router(module.router)


@app.get("/", tags=["Meta"], summary="Service metadata")
async def root() -> dict:
    return {
        "service": "valuegraph-datasets",
        "version": app.version,
        "markets": ["US", "KR"],
        "docs": "/docs",
        "disclaimer": "Not investment advice. Prices are delayed/EOD by default.",
    }


@app.get("/health", tags=["Meta"], summary="Health check")
async def health() -> dict:
    return {"status": "ok"}
