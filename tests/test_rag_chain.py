"""Pruebas sin red de la ingesta y de la cadena RAG.

Los embeddings se sustituyen por DeterministicFakeEmbedding y el LLM por un Runnable que devuelve
respuestas fijas, así que no se descarga ningún modelo ni se gasta saldo.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402
from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402

import ingesta  # noqa: E402
from chain import SalidaInvalidaError  # noqa: E402
from rag_chain import MAX_INTENTOS, TOP_K, RecuperadorSemantico, build_rag_chain, get_rag_response  # noqa: E402

DATA_REAL = Path(__file__).resolve().parent.parent / "data"


def _llm(respuestas: list[str]):
    """LLM simulado: devuelve una respuesta por llamada (repite la última) y guarda los prompts recibidos."""
    estado = {"n": 0, "prompts": []}

    def responder(prompt):
        estado["prompts"].append(prompt.to_string())
        estado["n"] += 1
        return AIMessage(content=respuestas[min(estado["n"], len(respuestas)) - 1])

    return RunnableLambda(responder), estado


def _json(**campos) -> str:
    return "```json\n" + json.dumps(campos, ensure_ascii=False) + "\n```"


class BaseConIndice(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._tmp.name)
        self.data = self.dir / "data"
        self.data.mkdir()
        for p in DATA_REAL.glob("*.md"):
            (self.data / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        self.store = self.dir / "vectorstore"
        self.embeddings = DeterministicFakeEmbedding(size=64)
        # Splitter por caracteres para no descargar el tokenizador en las pruebas.
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=150)

    def tearDown(self):
        self._tmp.cleanup()

    def indexar(self, **kw):
        return ingesta.ingest(self.data, self.store, "prueba", embeddings=self.embeddings,
                              splitter=self.splitter, **kw)

    def retriever(self):
        vs = ingesta.abrir_vectorstore(self.embeddings, self.store, "prueba")
        return RecuperadorSemantico(vectorstore=vs, embeddings=self.embeddings)


class TestIngesta(BaseConIndice):
    def test_indexa_y_no_reindexa_si_nada_cambio(self):
        primera = self.indexar()
        self.assertTrue(primera.reindexado)
        self.assertEqual(primera.documentos, 4)
        self.assertGreater(primera.fragmentos, 4)

        segunda = self.indexar()
        self.assertFalse(segunda.reindexado)
        self.assertEqual(segunda.fragmentos, primera.fragmentos)

    def test_reindexa_si_un_documento_cambia_o_con_rebuild(self):
        self.indexar()
        self.assertTrue(self.indexar(rebuild=True).reindexado)
        (self.data / "05_nuevo.txt").write_text("Documento nuevo sobre Atlas.", encoding="utf-8")
        resultado = self.indexar()
        self.assertTrue(resultado.reindexado)
        self.assertEqual(resultado.documentos, 5)

    def test_ids_estables_y_metadatos(self):
        self.indexar()
        docs = self.retriever().invoke("SEV1")
        self.assertEqual(len(docs), TOP_K)
        for d in docs:
            self.assertEqual(d.metadata["id"], f"{d.metadata['fuente']}#{d.metadata['fragmento']}")
            self.assertLessEqual(d.metadata["similitud"], 1.0)

    def test_rechaza_embeddings_distintos(self):
        self.indexar()
        coleccion = ingesta._cliente(self.store).get_collection("prueba")
        coleccion.modify(metadata={**coleccion.metadata, "embedding_model": "otro-modelo"})
        with self.assertRaises(RuntimeError):
            ingesta.abrir_vectorstore(self.embeddings, self.store, "prueba")

    def test_coleccion_inexistente(self):
        with self.assertRaises(FileNotFoundError):
            ingesta.abrir_vectorstore(self.embeddings, self.store, "no_existe")


class TestChunkingConTokenizador(unittest.TestCase):
    def test_fragmentos_de_hasta_500_tokens(self):
        try:
            splitter = ingesta.build_splitter()
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(ingesta.EMBEDDING_MODEL)
        except Exception as exc:  # noqa: BLE001 - sin red y sin el tokenizador en caché
            self.skipTest(f"tokenizador no disponible: {exc}")
        fragmentos = ingesta.fragmentar(ingesta.cargar_documentos(DATA_REAL), splitter)
        tamanos = [len(tokenizer.encode(f.page_content, add_special_tokens=False)) for f in fragmentos]
        self.assertLessEqual(max(tamanos), ingesta.CHUNK_SIZE)
        self.assertGreater(max(tamanos), 300)  # los fragmentos se llenan, no quedan diminutos


class TestCadenaRAG(BaseConIndice):
    def setUp(self):
        super().setUp()
        self.indexar()
        self.ids = [d.metadata["id"] for d in self.retriever().invoke("¿Cuánto tiempo para reconocer un SEV1?")]

    def preguntar(self, respuestas: list[str], pregunta="¿Cuánto tiempo para reconocer un SEV1?"):
        llm, estado = _llm(respuestas)
        cadena = build_rag_chain(self.retriever(), model=llm).with_config(max_concurrency=1)
        return asyncio.run(get_rag_response(pregunta, chain=cadena)), estado

    def test_respuesta_con_referencias_verificadas(self):
        resultado, estado = self.preguntar(
            [_json(respuesta="5 minutos.", encontrada=True, referencias=[self.ids[0], "inventado.md#9"])]
        )
        self.assertTrue(resultado.encontrada)
        self.assertEqual([r.id for r in resultado.referencias], [self.ids[0]])  # la inventada se descarta
        self.assertTrue(resultado.referencias[0].extracto)
        # El prompt incluye los fragmentos recuperados, la regla de "No lo sé" y las instrucciones del parser.
        prompt = estado["prompts"][0]
        self.assertIn(f'<fragmento id="{self.ids[0]}"', prompt)
        self.assertIn("No lo sé", prompt)
        self.assertIn('"referencias"', prompt)

    def test_no_lo_se_sin_referencias(self):
        resultado, _ = self.preguntar(
            [_json(respuesta="No lo sé. El contexto no menciona el proveedor de SMS.", encontrada=False,
                   referencias=[self.ids[0]])],
            pregunta="¿Qué proveedor de SMS se usa?",
        )
        self.assertFalse(resultado.encontrada)
        self.assertTrue(resultado.respuesta.startswith("No lo sé"))
        self.assertEqual(resultado.referencias, [])

    def test_reintenta_si_el_json_es_invalido(self):
        resultado, estado = self.preguntar(
            ["esto no es JSON", _json(respuesta="5 minutos.", encontrada=True, referencias=[self.ids[1]])]
        )
        self.assertEqual(estado["n"], 2)
        self.assertEqual(resultado.referencias[0].id, self.ids[1])

    def test_falla_si_nunca_cita_fragmentos_reales(self):
        with self.assertRaises(SalidaInvalidaError):
            self.preguntar([_json(respuesta="5 minutos.", encontrada=True, referencias=["inventado.md#0"])])

    def test_numero_de_intentos(self):
        llm, estado = _llm([_json(respuesta="x", encontrada=True, referencias=[])])
        cadena = build_rag_chain(self.retriever(), model=llm)
        with self.assertRaises(SalidaInvalidaError):
            asyncio.run(get_rag_response("pregunta", chain=cadena))
        self.assertEqual(estado["n"], MAX_INTENTOS)

    def test_pregunta_vacia(self):
        with self.assertRaises(ValueError):
            asyncio.run(get_rag_response("   ", chain=RunnableLambda(lambda x: x)))


if __name__ == "__main__":
    unittest.main()
