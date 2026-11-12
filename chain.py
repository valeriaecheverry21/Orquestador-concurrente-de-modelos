"""Pipeline de extracción de entidades técnicas: Prompt + LLM + salida estructurada (LCEL).

    chain = prompt | model.with_structured_output(EntidadesTecnicas, include_raw=True) | validar
    chain = chain.with_retry(...)

`include_raw=True` hace que la cadena entregue también el mensaje crudo del modelo, para poder
revisar el `finish_reason` (respuesta cortada por falta de tokens) antes de aceptar el objeto.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

import openai
from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda

from schemas import DEFAULT_MODELS, EntidadesTecnicas, ModelConfig

logger = logging.getLogger("pipeline")

MAX_INTENTOS = 3  # 1 llamada + 2 reintentos

# Valores de finish_reason / stop_reason que indican que el modelo no terminó su respuesta.
RAZONES_DE_CORTE = {"length", "max_tokens", "model_context_window_exceeded"}

INSTRUCCIONES_FORMATO = (
    "- tecnologias: solo nombres propios de tecnologías que aparezcan en el texto; no inventes ninguna.\n"
    "- nivel_de_criticidad: exactamente uno de 'baja', 'media' o 'alta'.\n"
    "- resumen_tecnico: una o dos frases en español, sin copiar el texto literal.\n"
    "- Si el texto es ambiguo, elige la interpretación más probable y refleja la duda en el resumen."
)

PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Eres un ingeniero de software sénior. Analizas textos técnicos (descripciones de "
            "arquitectura, logs de error, reportes de incidentes) y extraes sus entidades técnicas.\n\n"
            "Instrucciones de formato:\n{instrucciones_formato}",
        ),
        ("human", "Texto a analizar:\n\n{texto}"),
    ]
).partial(instrucciones_formato=INSTRUCCIONES_FORMATO)


class SalidaInvalidaError(ValueError):
    """El modelo devolvió un JSON mal formado o que no cumple el esquema."""


class RespuestaIncompletaError(SalidaInvalidaError):
    """El modelo cortó la respuesta (p. ej. por falta de tokens)."""


# Con OpenAI, el SDK lanza LengthFinishReasonError si finish_reason="length" al parsear la salida
# estructurada (antes de llegar a _validar); se trata igual que una respuesta incompleta.
ERRORES_REINTENTABLES = (SalidaInvalidaError, openai.LengthFinishReasonError)


def build_model(provider: str | None = None, **overrides: Any) -> BaseChatModel:
    """Crea el modelo de chat reutilizando la configuración validada del Módulo 1 (ModelConfig + .env)."""
    load_dotenv()
    provider = provider or os.getenv("LLM_PROVIDER", "anthropic")
    if provider not in DEFAULT_MODELS:  # p. ej. LLM_PROVIDER=all
        provider = "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "openai"
    env = {
        "provider": provider,
        "model": os.getenv(f"{provider.upper()}_MODEL"),
        "max_tokens": os.getenv("LLM_MAX_TOKENS"),
        "timeout": os.getenv("LLM_TIMEOUT"),
        "max_retries": os.getenv("LLM_MAX_RETRIES"),
    }
    config = ModelConfig(**({k: v for k, v in env.items() if v not in (None, "")} | overrides))

    # max_retries del SDK cubre los errores de red / rate limit; .with_retry() cubre la validación.
    comunes: dict[str, Any] = {
        "model": config.model_name,
        "max_tokens": config.max_tokens,
        "timeout": config.timeout,
        "max_retries": config.max_retries,
    }
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(temperature=0, **comunes)
    from langchain_anthropic import ChatAnthropic

    # Los modelos actuales de Claude rechazan `temperature`, así que no se envía.
    return ChatAnthropic(**comunes)


def _validar(salida: dict[str, Any], config: RunnableConfig) -> EntidadesTecnicas:
    """Revisa el finish_reason y el resultado del parser. Lanza un error reintentable si algo falla."""
    crudo = salida.get("raw")
    metadatos = getattr(crudo, "response_metadata", None) or {}
    razon = metadatos.get("finish_reason") or metadatos.get("stop_reason")
    logger.info("Respuesta del modelo recibida (finish_reason=%s)", razon)

    if razon in RAZONES_DE_CORTE:
        logger.warning("Respuesta incompleta: el modelo cortó la salida (finish_reason=%s)", razon)
        raise RespuestaIncompletaError(f"respuesta cortada por el modelo (finish_reason={razon})")

    if (error := salida.get("parsing_error")) is not None:
        logger.warning("La salida no cumple el esquema: %s", error)
        raise SalidaInvalidaError(str(error)) from error

    resultado = salida.get("parsed")
    if not isinstance(resultado, EntidadesTecnicas):
        logger.warning("El modelo no devolvió un objeto estructurado")
        raise SalidaInvalidaError("el modelo no devolvió un objeto estructurado")

    logger.info("Validación Pydantic correcta: %d tecnologías, criticidad=%s",
                len(resultado.tecnologias), resultado.nivel_de_criticidad.value)
    return resultado


def build_chain(model: BaseChatModel | None = None, *, structured: Runnable | None = None) -> Runnable:
    """Ensambla la cadena LCEL con reintentos.

    `structured` permite inyectar un sustituto de `model.with_structured_output(...)` en las pruebas.
    """
    if structured is None:
        structured = (model or build_model()).with_structured_output(EntidadesTecnicas, include_raw=True)
    chain = PROMPT | structured | RunnableLambda(_validar)
    return chain.with_retry(
        retry_if_exception_type=ERRORES_REINTENTABLES,
        stop_after_attempt=MAX_INTENTOS,
        wait_exponential_jitter=True,
    )


@lru_cache(maxsize=1)
def _default_chain() -> Runnable:
    return build_chain()


async def process_text(text: str, chain: Runnable | None = None) -> EntidadesTecnicas:
    """Extrae las entidades técnicas de `text` y devuelve un objeto validado.

    Lanza SalidaInvalidaError (o RespuestaIncompletaError) si tras los reintentos no hay salida válida.
    """
    if not text or not text.strip():
        raise ValueError("el texto de entrada no puede estar vacío")
    chain = chain or _default_chain()
    logger.info("Procesando texto de %d caracteres (hasta %d intentos)", len(text), MAX_INTENTOS)
    try:
        resultado = await chain.ainvoke({"texto": text})
    except openai.LengthFinishReasonError as exc:
        logger.error("Sin salida válida tras %d intentos: respuesta cortada (finish_reason=length)", MAX_INTENTOS)
        raise RespuestaIncompletaError("respuesta cortada por el modelo (finish_reason=length)") from exc
    except SalidaInvalidaError as exc:
        logger.error("Sin salida válida tras %d intentos: %s", MAX_INTENTOS, exc)
        raise
    logger.info("Extracción completada")
    return resultado
