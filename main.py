"""Script de validación: pregunta "¿Qué es la entropía?" en modo normal y en streaming.

Uso:
    python main.py                      # proveedor de LLM_PROVIDER (por defecto: ambos)
    python main.py --provider anthropic
    python main.py --provider all
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv
from pydantic import ValidationError

from llm_clients import AsyncLLMManager
from schemas import ChatMessage, Role

PREGUNTA = "¿Qué es la entropía?"
MENSAJES = [
    ChatMessage(role=Role.SYSTEM, content="Responde en español, de forma clara y en un máximo de 3 frases."),
    ChatMessage(role=Role.USER, content=PREGUNTA),
]


async def probar(provider: str) -> None:
    print(f"\n{'=' * 60}\n Proveedor: {provider}\n{'=' * 60}")
    try:
        manager = AsyncLLMManager.from_env(provider=provider)
    except ValidationError as exc:
        print(f"Configuración inválida:\n{exc}")
        return

    async with manager:
        print(f"Modelo: {manager.config.model_name}\n")

        # 1) Modo normal
        print(f"[normal] {PREGUNTA}")
        resp = await manager.generate(MENSAJES)
        if resp.ok:
            print(resp.content)
            tokens = resp.usage.total_tokens if resp.usage else "?"
            print(f"\n  -> {resp.latency_ms:.0f} ms | {tokens} tokens | intentos: {resp.attempts}")
        else:
            print(f"  ERROR controlado [{resp.error.type}]: {resp.error.message}")

        # 2) Modo streaming
        print(f"\n[streaming] {PREGUNTA}")
        async for chunk in manager.stream(MENSAJES):
            if chunk.error:
                print(f"\n  ERROR controlado [{chunk.error.type}]: {chunk.error.message}")
            else:
                print(chunk.text, end="", flush=True)
        print()


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Prueba del cliente LLM asíncrono")
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "all"],
        default=os.getenv("LLM_PROVIDER", "all"),
    )
    args = parser.parse_args()

    providers = ["openai", "anthropic"] if args.provider == "all" else [args.provider]
    for provider in providers:
        await probar(provider)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
