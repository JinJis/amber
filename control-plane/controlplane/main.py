"""Control-plane app: admin management + the data gateway.

Startup inits the store and best-effort loads the data-plane catalog (for
entitlement resolution). The gateway catch-all is included LAST so the admin and
meta routes match first.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from controlplane import admin, gateway
from controlplane.catalog_index import load_catalog_from_datasets
from controlplane.db import init_db
from controlplane.logging_config import install_request_logging, setup_logging

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    await load_catalog_from_datasets()
    yield


app = FastAPI(
    title="Investment-Agent Platform — Control Plane",
    version="0.1.0",
    description="Multi-tenant entitlements, metering, and gateway in front of the datasets data plane.",
    lifespan=lifespan,
)
install_request_logging(app)


@app.exception_handler(StarletteHTTPException)
async def _http_exc(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": "Error", "message": exc.detail})


@app.exception_handler(RequestValidationError)
async def _validation_exc(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    return JSONResponse(status_code=400, content={"error": "Bad Request", "message": first.get("msg", "Invalid request.")})


@app.get("/health", tags=["Meta"])
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", tags=["Meta"])
async def root() -> dict:
    return {"service": "control-plane", "version": app.version, "docs": "/docs"}


app.include_router(admin.router)
app.include_router(gateway.router)  # catch-all — must be last
