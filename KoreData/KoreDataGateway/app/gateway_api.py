# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# gateway api module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Primary types: SearchRequest, FullTextRequest, SentenceRequest.
# Function inventory:
# - register_gateway_api_routes: Registers gateway api routes for this module.
# - api_search: Implements the api search operation for this module.
# - api_full_text: Implements the api full text operation for this module.
# - api_sentence: Implements the api sentence operation for this module.
# - api_sentence_get: Implements the api sentence get operation for this module.
# ====================================================================================================

"""HTTP request models and route registration for KoreDataGateway.

The gateway server owns process lifecycle and MCP tools.  This module owns the
small HTTP adapter layer, keeping those concerns independent and testable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query:     str
    domains:   list[str] = Field(default_factory=list)
    since:     str | None = None
    until:     str | None = None
    mode:      str = "keyword"
    min_match: float = Field(default=0.4, ge=0.0, le=1.0)
    limit:     int = Field(default=20, ge=1, le=200)


class FullTextRequest(BaseModel):
    refid: str


class SentenceRequest(BaseModel):
    locator: str


SearchHandler   = Callable[[SearchRequest], Awaitable[dict[str, Any]]]
FullTextHandler = Callable[[str], Awaitable[dict[str, Any]]]
SentenceHandler = Callable[[str], Awaitable[dict[str, Any]]]


def register_gateway_api_routes(
    app: FastAPI,
    *,
    search: SearchHandler,
    get_full_text: FullTextHandler,
    get_sentence: SentenceHandler,
) -> None:
    """Register the gateway's HTTP API without coupling it to its runtime state."""

    @app.post("/api/search")
    async def api_search(request: SearchRequest) -> dict[str, Any]:
        return await search(request)

    @app.post("/api/full-text")
    async def api_full_text(request: FullTextRequest) -> dict[str, Any]:
        return await get_full_text(request.refid)

    @app.post("/api/sentence")
    async def api_sentence(request: SentenceRequest) -> dict[str, Any]:
        return await get_sentence(request.locator)

    @app.get("/api/sentence/{locator:path}")
    async def api_sentence_get(locator: str) -> dict[str, Any]:
        return await get_sentence(locator)
