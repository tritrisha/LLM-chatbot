import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated
from weakref import WeakValueDictionary

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints, field_validator

from .gateway import Gateway, ProvidersUnavailable
from .storage import Storage

SessionId = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"\S")]
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    session_id: SessionId
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    system_prompt: str | None = None

    @field_validator("message", "session_id", "system_prompt")
    @classmethod
    def validate_text(cls, value):
        if value is not None:
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("Text must contain valid Unicode") from None
        return value


def parse_simulation(header, key_count):
    statuses = {"429", "500", "502", "503"}
    valid_keys = {"*", *(f"key_{i}" for i in range(1, key_count + 1))}
    result = {}
    for entry in header.split(","):
        key, _, status = entry.strip().partition("=")
        if key not in valid_keys or status not in statuses or key in result:
            raise HTTPException(422, "invalid X-Simulate-Failure header")
        result[key] = int(status)
    return result


def create_app():
    @asynccontextmanager
    async def lifespan(app):
        keys = tuple(dict.fromkeys(k.strip() for k in os.getenv("LLM_API_KEYS", "").split(",") if k.strip()))
        app.state.gateway = Gateway(
            os.getenv("LLM_MODEL", "gemini/gemini-3.5-flash-lite"),
            keys,
            float(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
            api_base=os.getenv("LLM_API_BASE") or None,
        )
        app.state.storage = Storage(os.getenv("DATABASE_URL", "sqlite:///./data/app.db"))
        app.state.storage.initialize()
        app.state.locks = WeakValueDictionary()
        app.state.simulation = os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true"
        app.state.default_prompt = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful, concise assistant.")
        yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Simulate-Failure"],
    )

    @app.exception_handler(sqlite3.Error)
    async def storage_error(_request: Request, error: sqlite3.Error):
        logger.error("Database operation failed (%s)", type(error).__name__)
        return JSONResponse({"detail": "storage unavailable"}, status_code=503)

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, error: Exception):
        logger.error("Request failed (%s)", type(error).__name__)
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    @app.middleware("http")
    async def measure_request(request, call_next):
        request.state.started = perf_counter()
        return await call_next(request)

    @app.get("/health")
    async def health():
        await asyncio.to_thread(app.state.storage.check)
        keys = len(app.state.gateway.keys)
        if keys < 2:
            raise HTTPException(503, "at least two distinct provider keys are required")
        return {"status": "ok", "keys_configured": keys, "cache_enabled": True}

    @app.get("/sessions/{session_id}/messages")
    async def history(session_id: SessionId):
        _, messages = await asyncio.to_thread(app.state.storage.read, session_id)
        if not messages:
            raise HTTPException(404, "session not found")
        return {"session_id": session_id, "messages": messages}

    @app.post("/chat")
    async def chat(
        body: ChatRequest,
        request: Request,
        failure: Annotated[str | None, Header(alias="X-Simulate-Failure")] = None,
    ):
        gateway = app.state.gateway
        simulation = parse_simulation(failure, len(gateway.keys)) if failure is not None and app.state.simulation else {}
        lock = app.state.locks.setdefault(body.session_id, asyncio.Lock())
        async with lock:
            storage = app.state.storage
            saved_prompt, history = await asyncio.to_thread(storage.read, body.session_id)
            prompt = body.system_prompt if body.system_prompt is not None else saved_prompt
            if prompt is None:
                prompt = app.state.default_prompt
            messages = [
                {"role": "system", "content": prompt},
                *({"role": turn["role"], "content": turn["content"]} for turn in history),
                {"role": "user", "content": body.message},
            ]
            try:
                reply, metadata = await gateway.reply(messages, simulation, request.state.started)
            except ProvidersUnavailable:
                raise HTTPException(503, "all providers unavailable") from None
            await asyncio.to_thread(storage.append, body.session_id, prompt, body.message, reply, metadata)
        return {"session_id": body.session_id, "reply": reply, "metadata": metadata}

    return app


app = create_app()
