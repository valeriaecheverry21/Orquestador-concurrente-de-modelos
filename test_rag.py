"""Pruebas end-to-end de la cadena RAG (usa la API real del LLM).

Uso:
    python test_rag.py

1. Pregunta cuya respuesta SÍ está en los documentos: debe responder con los datos correctos y citar fuentes.
2. Pregunta trampa: el tema es de Atlas, pero el dato NO está en los documentos. El modelo debe responder
   "No lo sé" sin referencias, en lugar de inventar un proveedor.
"""

from __future__ import annotations

import asyncio
import sys

from ingesta import configurar_logs
from rag_chain import get_rag_response
from schemas import NO_LO_SE, RespuestaRAG

PREGUNTA_EN_DOCUMENTOS = (
    "¿En cuánto tiempo hay que reconocer una alerta SEV1 y en qué plazo debe publicarse el postmortem?"
)
DATOS_ESPERADOS = ("5 minutos", "5 días hábiles")

# atlas-notify envía SMS (está en el manual), pero el proveedor de SMS no se menciona en ningún documento.
PREGUNTA_TRAMPA = "¿Qué proveedor de SMS usa atlas-notify para enviar los mensajes a los clientes?"


def mostrar(titulo: str, resultado: RespuestaRAG) -> None:
    print(f"\n{'=' * 70}\n {titulo}\n{'=' * 70}")
    print(resultado.model_dump_json(indent=2))


def verificar(nombre: str, condicion: bool) -> bool:
    print(f"[{'OK' if condicion else 'FALLO'}] {nombre}")
    return condicion


async def main() -> int:
    configurar_logs()

    # Las dos preguntas se lanzan a la vez: la cadena es asíncrona de punta a punta.
    try:
        real, trampa = await asyncio.gather(
            get_rag_response(PREGUNTA_EN_DOCUMENTOS), get_rag_response(PREGUNTA_TRAMPA)
        )
    except Exception as exc:  # noqa: BLE001 - API key ausente, sin saldo, error de red...
        print(f"\n[ERROR DE LA API] {type(exc).__name__}: {exc}")
        return 2
    mostrar("1) Pregunta con respuesta en los documentos", real)
    mostrar("2) Pregunta trampa (respuesta fuera de los documentos)", trampa)

    print(f"\n{'=' * 70}\n Verificaciones\n{'=' * 70}")
    ok = all([
        verificar("1: encontrada = true", real.encontrada),
        verificar(f"1: contiene {DATOS_ESPERADOS}", all(d in real.respuesta for d in DATOS_ESPERADOS)),
        verificar("1: cita 03_gestion_de_incidentes.md",
                  any(r.fuente == "03_gestion_de_incidentes.md" for r in real.referencias)),
        verificar("2: encontrada = false", not trampa.encontrada),
        verificar(f"2: la respuesta empieza con '{NO_LO_SE}'", trampa.respuesta.startswith(NO_LO_SE)),
        verificar("2: sin referencias", not trampa.referencias),
    ])
    print("\nResultado:", "todas las verificaciones pasaron" if ok else "hay verificaciones fallidas")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
