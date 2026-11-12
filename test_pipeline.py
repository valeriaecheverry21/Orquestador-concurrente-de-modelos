"""Mini-script de prueba asíncrono del pipeline de extracción (usa la API real).

Uso:
    python test_pipeline.py

1. Texto claro: descripción de arquitectura con un cuello de botella.
2. Prueba de estrés: texto ambiguo, casi sin tecnologías.
3. Respuesta cortada: se limita max_tokens para forzar finish_reason de corte y ver los reintentos.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from chain import SalidaInvalidaError, build_chain, build_model, process_text

TEXTO_CLARO = (
    "Nuestra API está hecha con FastAPI y usa Redis como caché y PostgreSQL para la persistencia. "
    "Desde ayer, con más de 500 usuarios concurrentes, el pool de conexiones a PostgreSQL se agota "
    "y la API responde 503: 'FATAL: remaining connection slots are reserved'."
)

TEXTO_AMBIGUO = "Eso de anoche se cayó otra vez, creo que fue lo de la cola o algo del servidor. Ya lo reinicié y anda."


async def ejecutar(titulo: str, texto: str, chain=None) -> None:
    print(f"\n{'=' * 70}\n {titulo}\n{'=' * 70}\n{texto}\n")
    try:
        resultado = await process_text(texto, chain=chain)
    except SalidaInvalidaError as exc:
        print(f"\n[ERROR CONTROLADO] {type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - API key ausente, sin saldo, error de red...
        print(f"\n[ERROR DE LA API] {type(exc).__name__}: {exc}")
    else:
        print("\n" + resultado.model_dump_json(indent=2))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    await ejecutar("1) Texto claro", TEXTO_CLARO)
    await ejecutar("2) Prueba de estrés: texto ambiguo", TEXTO_AMBIGUO)
    await ejecutar(
        "3) Respuesta cortada (max_tokens=15): se detecta por finish_reason y se reintenta",
        TEXTO_CLARO,
        chain=build_chain(build_model(max_tokens=15)),
    )


if __name__ == "__main__":
    asyncio.run(main())
