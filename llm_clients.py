"""Clientes asíncronos de LLM con una interfaz común (OpenAI y Anthropic)."""

from __future__ import annotations

import asyncio
import os
import random
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any, ClassVar

import anthropic
import openai

from schemas import (
    ChatMessage,
    ErrorType,
    LLMError,
    ModelConfig,
    ModelResponse,
    Role,
    StreamChunk,
    TokenUsage,
)

Messages = str | Sequence[ChatMessage]


def _to_messages(messages: Messages) -> list[ChatMessage]:
    """Acepta un texto suelto o una lista de ChatMessage."""
    if isinstance(messages, str):
        return [ChatMessage(role=Role.USER, content=messages)]
    return list(messages)


def map_sdk_error(exc: Exception, sdk: Any) -> LLMError:
    """Convierte una excepción de los SDK (openai/anthropic comparten jerarquía) en un LLMError.

    El orden importa: de la clase más específica a la más general.
    """
    status = getattr(exc, "status_code", None)
    msg = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__

    if isinstance(exc, sdk.AuthenticationError):
        return LLMError(type=ErrorType.AUTHENTICATION, message=f"API key inválida: {msg}", status_code=status)
    if isinstance(exc, sdk.PermissionDeniedError):
        return LLMError(type=ErrorType.PERMISSION, message=msg, status_code=status)
    if isinstance(exc, sdk.NotFoundError):
        return LLMError(type=ErrorType.NOT_FOUND, message=f"Modelo o recurso no encontrado: {msg}", status_code=status)
    if isinstance(exc, sdk.RateLimitError):
        # OpenAI devuelve 429 también cuando se acaba el saldo; reintentar no sirve.
        if getattr(exc, "code", None) == "insufficient_quota":
            return LLMError(type=ErrorType.QUOTA, message=f"Cuota agotada: {msg}", status_code=status)
        return LLMError(type=ErrorType.RATE_LIMIT, message=msg, retryable=True, status_code=status)
    if isinstance(exc, sdk.BadRequestError):
        return LLMError(type=ErrorType.BAD_REQUEST, message=msg, status_code=status)
    if isinstance(exc, sdk.APIStatusError):
        if status is not None and (status >= 500 or status in (408, 409)):
            return LLMError(type=ErrorType.SERVER, message=msg, retryable=True, status_code=status)
        return LLMError(type=ErrorType.BAD_REQUEST, message=msg, status_code=status)
    if isinstance(exc, sdk.APITimeoutError):  # subclase de APIConnectionError: va antes
        return LLMError(type=ErrorType.TIMEOUT, message="La petición superó el tiempo límite", retryable=True)
    if isinstance(exc, sdk.APIConnectionError):
        return LLMError(type=ErrorType.CONNECTION, message=f"Error de red: {msg}", retryable=True)
    return LLMError(type=ErrorType.UNKNOWN, message=f"{exc.__class__.__name__}: {msg}")


class BaseLLMClient(ABC):
    """Interfaz común. `generate` y `stream` nunca lanzan errores de la API: los devuelven estructurados."""

    provider: ClassVar[str]
    api_key_env: ClassVar[str]

    def __init__(self, config: ModelConfig, api_key: str | None = None, **sdk_kwargs: Any) -> None:
        if config.provider != self.provider:
            raise ValueError(f"{type(self).__name__} requiere provider='{self.provider}', recibió '{config.provider}'")
        self.config = config
        key = api_key or os.getenv(self.api_key_env)
        self._sdk: Any = self._build_sdk_client(key, **sdk_kwargs) if key else None

    # --- a implementar por cada proveedor ---------------------------------
    @abstractmethod
    def _build_sdk_client(self, api_key: str, **kwargs: Any) -> Any: ...

    @abstractmethod
    async def _generate_once(self, messages: list[ChatMessage]) -> ModelResponse: ...

    @abstractmethod
    def _stream_once(self, messages: list[ChatMessage]) -> AsyncIterator[str]: ...

    @abstractmethod
    def _map_error(self, exc: Exception) -> LLMError: ...

    # --- API pública -------------------------------------------------------
    async def generate(self, messages: Messages) -> ModelResponse:
        """Devuelve la respuesta completa. Reintenta con backoff los errores transitorios."""
        msgs = _to_messages(messages)
        start = time.perf_counter()
        error = self._missing_key_error()
        attempt = 0
        while error is None or (error.retryable and attempt <= self.config.max_retries):
            if attempt > 0:
                await self._backoff(attempt)
            attempt += 1
            try:
                response = await self._generate_once(msgs)
                response.latency_ms = (time.perf_counter() - start) * 1000
                response.attempts = attempt
                return response
            except Exception as exc:  # noqa: BLE001 - se convierte en error controlado
                error = self._map_error(exc)
        return ModelResponse(
            provider=self.provider,  # type: ignore[arg-type]
            model=self.config.model_name,
            latency_ms=(time.perf_counter() - start) * 1000,
            attempts=attempt,
            error=error,
        )

    async def stream(self, messages: Messages) -> AsyncIterator[StreamChunk]:
        """Generador asíncrono: emite fragmentos de texto conforme llegan.

        Solo reintenta si el fallo ocurre antes del primer fragmento (si no, el texto saldría duplicado).
        Ante un error emite un último StreamChunk con `error` y termina.
        """
        msgs = _to_messages(messages)
        if (error := self._missing_key_error()) is not None:
            yield StreamChunk(error=error)
            return
        attempt = 0
        while True:
            if attempt > 0:
                await self._backoff(attempt)
            attempt += 1
            emitted = False
            try:
                async for text in self._stream_once(msgs):
                    emitted = True
                    yield StreamChunk(text=text)
                return
            except Exception as exc:  # noqa: BLE001
                error = self._map_error(exc)
                if emitted or not error.retryable or attempt > self.config.max_retries:
                    yield StreamChunk(error=error)
                    return

    async def aclose(self) -> None:
        if self._sdk is not None:
            await self._sdk.close()

    async def __aenter__(self) -> BaseLLMClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # --- utilidades --------------------------------------------------------
    def _missing_key_error(self) -> LLMError | None:
        if self._sdk is None:
            return LLMError(type=ErrorType.CONFIG, message=f"Falta la variable de entorno {self.api_key_env}")
        return None

    async def _backoff(self, attempt: int) -> None:
        # Backoff exponencial con jitter: 1s, 2s, 4s... (+ hasta 0.5s aleatorio)
        base = self.config.retry_base_delay
        if base > 0:
            await asyncio.sleep(base * 2 ** (attempt - 1) + random.uniform(0, base / 2))


class OpenAIClient(BaseLLMClient):
    provider = "openai"
    api_key_env = "OPENAI_API_KEY"

    def _build_sdk_client(self, api_key: str, **kwargs: Any) -> openai.AsyncOpenAI:
        # max_retries=0: los reintentos los gestiona BaseLLMClient para controlarlos y contarlos.
        return openai.AsyncOpenAI(api_key=api_key, timeout=self.config.timeout, max_retries=0, **kwargs)

    def _params(self, messages: list[ChatMessage]) -> dict[str, Any]:
        return {
            "model": self.config.model_name,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "temperature": self.config.temperature,
            "max_completion_tokens": self.config.max_tokens,
        }

    async def _generate_once(self, messages: list[ChatMessage]) -> ModelResponse:
        completion = await self._sdk.chat.completions.create(**self._params(messages))
        choice = completion.choices[0]
        usage = completion.usage
        return ModelResponse(
            provider="openai",
            model=completion.model,
            content=choice.message.content or "",
            finish_reason=choice.finish_reason,
            usage=TokenUsage(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens) if usage else None,
        )

    async def _stream_once(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        stream = await self._sdk.chat.completions.create(**self._params(messages), stream=True)
        async for chunk in stream:
            if chunk.choices and (delta := chunk.choices[0].delta.content):
                yield delta

    def _map_error(self, exc: Exception) -> LLMError:
        return map_sdk_error(exc, openai)


class AnthropicClient(BaseLLMClient):
    """Cliente de Claude.

    Notas de la API actual de Anthropic:
    - Los modelos vigentes rechazan `temperature` (error 400), así que no se envía;
      se valida en ModelConfig igualmente para mantener la interfaz común.
    - El rol "system" va en el parámetro `system`, no dentro de `messages`.
    - Se activa el fallback del lado del servidor: si el modelo rechaza la petición
      por sus filtros de seguridad, la API la reintenta con otro modelo en la misma llamada.
    """

    provider = "anthropic"
    api_key_env = "ANTHROPIC_API_KEY"

    def _build_sdk_client(self, api_key: str, **kwargs: Any) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(api_key=api_key, timeout=self.config.timeout, max_retries=0, **kwargs)

    def _params(self, messages: list[ChatMessage]) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in messages if m.role is Role.SYSTEM)
        params: dict[str, Any] = {
            "model": self.config.model_name,
            "max_tokens": self.config.max_tokens,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages if m.role is not Role.SYSTEM],
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        }
        if system:
            params["system"] = system
        return params

    async def _generate_once(self, messages: list[ChatMessage]) -> ModelResponse:
        message = await self._sdk.beta.messages.create(**self._params(messages))
        # El contenido puede incluir bloques de "thinking"; nos quedamos solo con el texto.
        text = "".join(block.text for block in message.content if block.type == "text")
        return ModelResponse(
            provider="anthropic",
            model=message.model,
            content=text,
            finish_reason=message.stop_reason,
            usage=TokenUsage(input_tokens=message.usage.input_tokens, output_tokens=message.usage.output_tokens),
        )

    async def _stream_once(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        async with self._sdk.beta.messages.stream(**self._params(messages)) as stream:
            async for text in stream.text_stream:
                yield text

    def _map_error(self, exc: Exception) -> LLMError:
        return map_sdk_error(exc, anthropic)


CLIENTS: dict[str, type[BaseLLMClient]] = {
    "openai": OpenAIClient,
    "anthropic": AnthropicClient,
}


class AsyncLLMManager:
    """Punto de entrada único: elige el proveedor según la configuración (o la variable LLM_PROVIDER)."""

    def __init__(self, config: ModelConfig, api_key: str | None = None, **sdk_kwargs: Any) -> None:
        self.config = config
        self.client: BaseLLMClient = CLIENTS[config.provider](config, api_key=api_key, **sdk_kwargs)

    @classmethod
    def from_env(cls, provider: str | None = None, **overrides: Any) -> AsyncLLMManager:
        """Construye la configuración desde variables de entorno (LLM_PROVIDER, OPENAI_MODEL, ...).

        Pydantic valida los valores: p. ej. LLM_TEMPERATURE=3 lanza ValidationError.
        """
        provider = provider or os.getenv("LLM_PROVIDER", "openai")
        env = {
            "provider": provider,
            "model": os.getenv(f"{provider.upper()}_MODEL"),
            "temperature": os.getenv("LLM_TEMPERATURE"),
            "max_tokens": os.getenv("LLM_MAX_TOKENS"),
            "timeout": os.getenv("LLM_TIMEOUT"),
            "max_retries": os.getenv("LLM_MAX_RETRIES"),
        }
        data = {k: v for k, v in env.items() if v not in (None, "")} | overrides
        return cls(ModelConfig(**data))

    @property
    def provider(self) -> str:
        return self.config.provider

    async def generate(self, messages: Messages) -> ModelResponse:
        return await self.client.generate(messages)

    async def stream(self, messages: Messages) -> AsyncIterator[StreamChunk]:
        async for chunk in self.client.stream(messages):
            yield chunk

    async def aclose(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> AsyncLLMManager:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
