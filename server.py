"""Servidor web local: expone el cliente LLM por HTTP y sirve una página para probarlo.

Ejecutar:
    uvicorn server:app
y abrir http://localhost:8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from llm_clients import AsyncLLMManager
from schemas import ChatMessage, ModelResponse, Provider, Role

STATIC_DIR = Path(__file__).parent / "static"
SYSTEM_PROMPT = "Responde en español, de forma clara y concisa."

managers: dict[str, AsyncLLMManager] = {}
config_errors: dict[str, str] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    for provider in ("openai", "anthropic"):
        try:
            managers[provider] = AsyncLLMManager.from_env(provider=provider)
        except ValidationError as exc:
            config_errors[provider] = str(exc)
    yield
    for manager in managers.values():
        await manager.aclose()
    managers.clear()


app = FastAPI(title="Unified Async LLM Client", lifespan=lifespan)


class ChatRequest(BaseModel):
    provider: Provider
    message: str = Field(min_length=1, max_length=8000)


def _get_manager(provider: str) -> AsyncLLMManager:
    if provider in config_errors:
        raise HTTPException(500, f"Configuración inválida para {provider}: {config_errors[provider]}")
    return managers[provider]


def _messages(text: str) -> list[ChatMessage]:
    return [
        ChatMessage(role=Role.SYSTEM, content=SYSTEM_PROMPT),
        ChatMessage(role=Role.USER, content=text),
    ]


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/providers")
async def providers() -> list[dict]:
    """Qué proveedores hay y si tienen API key configurada."""
    out = []
    for name in ("openai", "anthropic"):
        manager = managers.get(name)
        out.append({
            "provider": name,
            "model": manager.config.model_name if manager else None,
            "configured": bool(manager and manager.client.is_configured),
            "error": config_errors.get(name),
        })
    return out


@app.post("/api/chat")
async def chat(req: ChatRequest) -> ModelResponse:
    """Modo normal: devuelve la respuesta completa."""
    return await _get_manager(req.provider).generate(_messages(req.message))


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Streaming: una línea JSON (StreamChunk) por fragmento, formato NDJSON."""
    manager = _get_manager(req.provider)

    async def body() -> AsyncIterator[str]:
        async for chunk in manager.stream(_messages(req.message)):
            yield chunk.model_dump_json(exclude_none=True) + "\n"

    return StreamingResponse(body(), media_type="application/x-ndjson")
