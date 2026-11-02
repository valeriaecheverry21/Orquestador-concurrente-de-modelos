"""Orquestador concurrente de modelos de IA (simulado) con asyncio.

Requiere Python 3.11+ (asyncio.timeout). Probado con Python 3.12.
"""

import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="[%(relativeCreated)7.0f ms] %(message)s",
)
log = logging.getLogger("orquestador")

TIMEOUT_TOTAL = 2.0     # segundos para toda la orquestación
MAX_CONCURRENTES = 2    # llamadas simultáneas permitidas por el semáforo


# --- 1. Simulación de llamadas a modelos ---------------------------------

async def _simular_modelo(nombre: str, latencia: float) -> str:
    log.info("-> %s: inicia (latencia %.1fs)", nombre, latencia)
    await asyncio.sleep(latencia)  # no bloquea el event loop
    log.info("<- %s: responde", nombre)
    return f"respuesta de {nombre}"


async def gpt_4_call(latencia: float = 1.2) -> str:
    return await _simular_modelo("gpt-4", latencia)


async def claude_3_call(latencia: float = 0.8) -> str:
    return await _simular_modelo("claude-3", latencia)


async def local_llama_call(latencia: float = 1.5) -> str:
    return await _simular_modelo("local-llama", latencia)


# --- 2 y 3. Orquestación con gather + timeout total ----------------------

async def orquestar(latencia_llama: float = 1.5) -> list[str] | None:
    try:
        async with asyncio.timeout(TIMEOUT_TOTAL):
            resultados = await asyncio.gather(
                gpt_4_call(),
                claude_3_call(),
                local_llama_call(latencia_llama),
            )
    except TimeoutError:
        log.error("ERROR: la orquestación superó %.1fs; llamadas canceladas",
                  TIMEOUT_TOTAL)
        return None
    log.info("OK: %s", resultados)
    return resultados


# --- 4. Control de flujo con Semaphore ------------------------------------

async def llamada_limitada(i: int, sem: asyncio.Semaphore, estado: dict) -> str:
    log.info("   tarea %2d: solicitada", i)
    async with sem:
        estado["activas"] += 1
        estado["max"] = max(estado["max"], estado["activas"])
        log.info("   tarea %2d: EJECUTANDO (activas=%d)", i, estado["activas"])
        try:
            await asyncio.sleep(0.3)
        finally:
            estado["activas"] -= 1
    return f"resultado {i}"


async def rafaga_con_semaforo(n: int = 10) -> None:
    sem = asyncio.Semaphore(MAX_CONCURRENTES)
    estado = {"activas": 0, "max": 0}
    try:
        async with asyncio.timeout(TIMEOUT_TOTAL):
            resultados = await asyncio.gather(
                *(llamada_limitada(i, sem, estado) for i in range(1, n + 1))
            )
        log.info("OK: %d resultados, concurrencia máxima observada = %d",
                 len(resultados), estado["max"])
    except TimeoutError:
        log.error("ERROR: la ráfaga superó %.1fs", TIMEOUT_TOTAL)


async def main() -> None:
    log.info("=== Escenario 1: todos los modelos responden a tiempo ===")
    await orquestar(latencia_llama=1.5)

    log.info("=== Escenario 2: local-llama tarda 3s (supera el timeout) ===")
    await orquestar(latencia_llama=3.0)
    log.info("El programa sigue ejecutándose tras el timeout")

    log.info("=== Escenario 3: 10 llamadas, máximo %d a la vez ===",
             MAX_CONCURRENTES)
    await rafaga_con_semaforo(10)


if __name__ == "__main__":
    asyncio.run(main())
