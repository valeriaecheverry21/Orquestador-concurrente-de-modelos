"""Modelos Pydantic: mensajes de entrada, configuración del modelo y respuestas."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Provider = Literal["openai", "anthropic"]

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-opus-5-5",
}


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    """Un mensaje de la conversación."""

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("el contenido no puede estar vacío")
        return v


class ModelConfig(BaseModel):
    """Parámetros del modelo, validados antes de llamar a la API."""

    model_config = ConfigDict(frozen=True)

    provider: Provider
    model: str | None = Field(default=None, description="Si es None se usa el modelo por defecto del proveedor")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, gt=0, le=128_000)
    timeout: float = Field(default=60.0, gt=0, description="Segundos por petición")
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_base_delay: float = Field(default=1.0, ge=0, description="Espera base del backoff exponencial")

    @property
    def model_name(self) -> str:
        return self.model or DEFAULT_MODELS[self.provider]


class ErrorType(StrEnum):
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER = "server"
    BAD_REQUEST = "bad_request"
    NOT_FOUND = "not_found"
    CONFIG = "config"
    UNKNOWN = "unknown"


class LLMError(BaseModel):
    """Error controlado: se devuelve en lugar de propagar la excepción."""

    type: ErrorType
    message: str
    retryable: bool = False
    status_code: int | None = None


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelResponse(BaseModel):
    """Respuesta unificada, igual para cualquier proveedor."""

    provider: Provider
    model: str
    content: str = ""
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    latency_ms: float = 0.0
    attempts: int = 1
    error: LLMError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class StreamChunk(BaseModel):
    """Fragmento de texto del streaming. Si `error` no es None, el stream terminó con un error."""

    text: str = ""
    error: LLMError | None = None
