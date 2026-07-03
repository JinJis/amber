"""Agent Engine API — run agents over a tenant's activated connectors + RAG.

The tenant API key is supplied per request (X-API-KEY) and used for every tool
call through the gateway, so entitlement + metering apply to agent activity too.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from agentengine.agent import refresh_artifact, run_agent
from agentengine.chat import stream_chat
from agentengine.client import PlatformClient
from agentengine.config import settings
from agentengine.logging_config import install_request_logging, setup_logging
from agentengine.models import (
    AgentSpec,
    ArtifactRefreshRequest,
    ChatRequest,
    CompileRequest,
    RunRequest,
)

setup_logging()

app = FastAPI(
    title="Platform Agent Engine", version="0.1.0",
    description="Run agents over activated connectors + RAG, via the gateway, with provenance + guardrails.",
)
install_request_logging(app)


@app.get("/health", tags=["Meta"])
async def health() -> dict:
    return {"status": "ok"}


@app.get("/agent/info", tags=["Agent"], summary="Active planner + config")
async def info() -> dict:
    return {"llm_backend": settings.llm_backend, "model": settings.model, "gateway_url": settings.gateway_url}


@app.post("/agent/run", tags=["Agent"], summary="Run an agent task (NL) over activated tools")
async def run(body: RunRequest, x_api_key: Annotated[str | None, Header(alias="X-API-KEY")] = None) -> dict:
    result = await run_agent(body.task, x_api_key, body.spec)
    return result.model_dump()


@app.post("/agent/artifact/refresh", tags=["Agent"], summary="Re-run a pinned artifact's tool to refresh it")
async def artifact_refresh(body: ArtifactRefreshRequest, x_api_key: Annotated[str | None, Header(alias="X-API-KEY")] = None) -> dict:
    a = await refresh_artifact(body.tool, body.args, x_api_key, body.title)
    if a is None:
        raise HTTPException(404, "Could not refresh — tool unavailable or produced no artifact.")
    return {"artifact": a.model_dump()}


@app.post("/agent/chat", tags=["Agent"], summary="Streaming multi-turn chat (SSE)")
async def chat(body: ChatRequest, x_api_key: Annotated[str | None, Header(alias="X-API-KEY")] = None) -> StreamingResponse:
    messages = [m.model_dump() for m in body.messages]

    async def gen():
        async for event in stream_chat(messages, x_api_key, body.spec):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/agent/compile", tags=["Agent"], summary="Natural-language → reusable AgentSpec")
async def compile_spec(body: CompileRequest) -> dict:
    # Stub compiler: wrap the description as a system prompt. A Gemini-backed
    # compiler can infer allowed_tools/steps later.
    return AgentSpec(system=body.description.strip()).model_dump()
