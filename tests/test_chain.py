"""Pruebas sin red del pipeline: el modelo se sustituye por un Runnable simulado."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from chain import MAX_INTENTOS, RespuestaIncompletaError, SalidaInvalidaError, build_chain, process_text  # noqa: E402
from schemas import EntidadesTecnicas, NivelCriticidad  # noqa: E402

VALIDO = EntidadesTecnicas(
    tecnologias=["FastAPI", "Redis"],
    nivel_de_criticidad="alta",
    resumen_tecnico="API con caché en Redis; se agotan las conexiones.",
)


def _salida(parsed=None, error=None, razon="stop"):
    return {"raw": AIMessage(content="", response_metadata={"finish_reason": razon}), "parsed": parsed, "parsing_error": error}


def _cadena(respuestas: list[dict]):
    """Cadena cuyo 'modelo' devuelve las respuestas dadas, una por intento."""
    llamadas = {"n": 0}

    def falso_modelo(_prompt):
        llamadas["n"] += 1
        return respuestas[min(llamadas["n"], len(respuestas)) - 1]

    return build_chain(structured=RunnableLambda(falso_modelo)), llamadas


class TestEsquema(unittest.TestCase):
    def test_valido(self):
        self.assertIs(VALIDO.nivel_de_criticidad, NivelCriticidad.ALTA)

    def test_tecnologias_vacias(self):
        for tecnologias in ([], ["  ", ""]):
            with self.assertRaises(ValidationError):
                EntidadesTecnicas(tecnologias=tecnologias, nivel_de_criticidad="baja", resumen_tecnico="Resumen válido.")

    def test_criticidad_fuera_del_enum(self):
        with self.assertRaises(ValidationError):
            EntidadesTecnicas(tecnologias=["Redis"], nivel_de_criticidad="crítica", resumen_tecnico="Resumen válido.")

    def test_quita_duplicados(self):
        e = EntidadesTecnicas(tecnologias=["Redis", " redis ", "Kafka"], nivel_de_criticidad="media", resumen_tecnico="Resumen válido.")
        self.assertEqual(e.tecnologias, ["Redis", "Kafka"])


class TestCadena(unittest.IsolatedAsyncioTestCase):
    async def test_respuesta_valida_al_primer_intento(self):
        chain, llamadas = _cadena([_salida(parsed=VALIDO)])
        self.assertEqual(await process_text("texto", chain=chain), VALIDO)
        self.assertEqual(llamadas["n"], 1)

    async def test_se_recupera_de_un_json_mal_formado(self):
        chain, llamadas = _cadena([_salida(error=ValueError("JSON mal formado")), _salida(parsed=VALIDO)])
        self.assertEqual(await process_text("texto", chain=chain), VALIDO)
        self.assertEqual(llamadas["n"], 2)

    async def test_respuesta_cortada_agota_los_reintentos(self):
        # Aunque venga un objeto "parseado", si el finish_reason indica corte no se acepta.
        chain, llamadas = _cadena([_salida(parsed=VALIDO, razon="length")])
        with self.assertRaises(RespuestaIncompletaError):
            await process_text("texto", chain=chain)
        self.assertEqual(llamadas["n"], MAX_INTENTOS)

    async def test_sin_objeto_estructurado(self):
        chain, _ = _cadena([_salida()])
        with self.assertRaises(SalidaInvalidaError):
            await process_text("texto", chain=chain)

    async def test_texto_vacio(self):
        with self.assertRaises(ValueError):
            await process_text("   ")


if __name__ == "__main__":
    unittest.main()
