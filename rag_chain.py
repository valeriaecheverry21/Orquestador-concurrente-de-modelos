"""Cadena RAG asíncrona: recuperación en ChromaDB + generación grounded con LCEL + PydanticOutputParser.

    pregunta ─► RecuperadorSemantico (embedding de la consulta → top-k en ChromaDB)
             ─► formatear_contexto ─► PROMPT ─► LLM ─► PydanticOutputParser(RespuestaGenerada)
             ─► _ensamblar (valida las referencias contra los fragmentos recuperados) ─► RespuestaRAG

Uso:
    python rag_chain.py "¿Cuánto tiempo hay para reconocer una alerta SEV1?"
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from operator import itemgetter
from typing import Any

from langchain_chroma import Chroma
from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough

from chain import SalidaInvalidaError, build_model
from ingesta import abrir_vectorstore, configurar_logs, get_embeddings, ingest
from schemas import NO_LO_SE, Referencia, RespuestaGenerada, RespuestaRAG

logger = logging.getLogger("rag")

TOP_K = 4  # entre 3 y 5: suficiente contexto sin "lost in the middle"
MAX_INTENTOS = 3

PARSER = PydanticOutputParser(pydantic_object=RespuestaGenerada)

SYSTEM_PROMPT = """Eres el asistente técnico del equipo de ingeniería de Atlas. Actúas como un filtro de veracidad.

Reglas:
1. Responde ÚNICAMENTE con la información del CONTEXTO. No uses conocimiento propio ni supongas datos.
2. Si el CONTEXTO no contiene la respuesta (o solo una parte), no la completes: en ese caso "encontrada" es false,
   "referencias" es una lista vacía y "respuesta" empieza exactamente con "{no_lo_se}", seguido como mucho de una
   frase que diga qué información falta.
3. Si la respuesta está en el CONTEXTO, "encontrada" es true y "referencias" contiene los id de TODOS los
   fragmentos que usaste (copiados tal cual del atributo id) y ningún otro.
4. Responde en español, de forma breve y precisa, con los mismos valores (números, plazos, nombres) del CONTEXTO.

{format_instructions}"""

HUMAN_PROMPT = """CONTEXTO:
{contexto}

PREGUNTA: {pregunta}"""

PROMPT = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", HUMAN_PROMPT)]).partial(
    no_lo_se=NO_LO_SE, format_instructions=PARSER.get_format_instructions()
)


class RecuperadorSemantico(BaseRetriever):
    """Convierte la pregunta en un embedding y recupera los `k` fragmentos más cercanos (similitud coseno).

    Usa el mismo objeto de embeddings que la ingesta. Cada documento devuelto lleva en sus metadatos
    `id` y `similitud` (1 = idéntico).
    """

    vectorstore: Chroma
    embeddings: Embeddings
    k: int = TOP_K

    def _buscar(self, vector: list[float]) -> list[Document]:
        resultados = self.vectorstore.similarity_search_by_vector_with_relevance_scores(vector, k=self.k)
        documentos = []
        for doc, distancia in resultados:  # Chroma devuelve la distancia coseno (menor = más parecido)
            doc.metadata = {**doc.metadata, "id": doc.id, "similitud": round(1 - distancia, 4)}
            documentos.append(doc)
        return documentos

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        return self._buscar(self.embeddings.embed_query(query))

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> list[Document]:
        vector = await self.embeddings.aembed_query(query)
        documentos = await asyncio.to_thread(self._buscar, vector)  # chromadb es síncrono: no bloquear el loop
        logger.info("Recuperados %d fragmentos: %s", len(documentos),
                    ", ".join(f"{d.metadata['id']} ({d.metadata['similitud']:.2f})" for d in documentos))
        return documentos


def formatear_contexto(documentos: list[Document]) -> str:
    if not documentos:
        return "(sin fragmentos)"
    return "\n\n".join(
        f'<fragmento id="{d.metadata["id"]}" fuente="{d.metadata["fuente"]}">\n{d.page_content}\n</fragmento>'
        for d in documentos
    )


def _ensamblar(entrada: dict[str, Any]) -> RespuestaRAG:
    """Cruza las referencias del modelo con los fragmentos recuperados y arma la respuesta final."""
    generada: RespuestaGenerada = entrada["generada"]
    por_id = {d.metadata["id"]: d for d in entrada["documentos"]}

    if not generada.encontrada:
        logger.info("El modelo indica que la respuesta no está en el contexto")
        return RespuestaRAG(pregunta=entrada["pregunta"], respuesta=generada.respuesta, encontrada=False)

    inventadas = [r for r in generada.referencias if r not in por_id]
    if inventadas:
        logger.warning("Referencias que no están entre los fragmentos recuperados (se descartan): %s", inventadas)
    validas = [r for r in dict.fromkeys(generada.referencias) if r in por_id]
    if not validas:
        raise SalidaInvalidaError("la respuesta dice estar en el contexto pero no cita ningún fragmento recuperado")

    referencias = [
        Referencia(
            id=r,
            fuente=por_id[r].metadata["fuente"],
            similitud=por_id[r].metadata["similitud"],
            extracto=por_id[r].page_content[:200],
        )
        for r in validas
    ]
    return RespuestaRAG(pregunta=entrada["pregunta"], respuesta=generada.respuesta, encontrada=True,
                        referencias=referencias)


def build_rag_chain(retriever: BaseRetriever, model: BaseChatModel | None = None) -> Runnable:
    """Ensambla la cadena LCEL. Entrada: {"pregunta": str}. Salida: RespuestaRAG."""
    model = model or build_model()
    generacion = (
        RunnablePassthrough.assign(contexto=itemgetter("documentos") | RunnableLambda(formatear_contexto))
        | PROMPT
        | model
        | PARSER
    )
    # Los reintentos cubren solo la generación (JSON inválido o referencias inválidas): no se repite la búsqueda.
    generar_y_validar = (RunnablePassthrough.assign(generada=generacion) | RunnableLambda(_ensamblar)).with_retry(
        retry_if_exception_type=(OutputParserException, SalidaInvalidaError),
        stop_after_attempt=MAX_INTENTOS,
        wait_exponential_jitter=True,
    )
    return RunnablePassthrough.assign(documentos=itemgetter("pregunta") | retriever) | generar_y_validar


def _recursos() -> Runnable:
    """Carga (una sola vez) el índice, el modelo de embeddings y el LLM."""
    ingest()  # no hace nada si la colección ya está al día
    embeddings = get_embeddings()
    retriever = RecuperadorSemantico(vectorstore=abrir_vectorstore(embeddings), embeddings=embeddings)
    return build_rag_chain(retriever)


_cadena_por_defecto: Runnable | None = None
_lock_carga = threading.Lock()


def _default() -> Runnable:
    """Inicialización perezosa y thread-safe: varias preguntas concurrentes cargan los recursos una sola vez."""
    global _cadena_por_defecto
    with _lock_carga:
        if _cadena_por_defecto is None:
            _cadena_por_defecto = _recursos()
        return _cadena_por_defecto


async def get_rag_response(query: str, chain: Runnable | None = None) -> RespuestaRAG:
    """Responde `query` usando solo los documentos indexados.

    a) búsqueda de similitud en ChromaDB, b) prompt con los fragmentos, c) llamada asíncrona al LLM,
    d) parseo a RespuestaRAG (texto + referencias).
    """
    if not query or not query.strip():
        raise ValueError("la pregunta no puede estar vacía")
    if chain is None:
        chain = await asyncio.to_thread(_default)  # la primera carga (modelo + Chroma) es lenta y síncrona
    logger.info("Pregunta: %s", query)
    return await chain.ainvoke({"pregunta": query.strip()})


if __name__ == "__main__":
    configurar_logs()
    pregunta = " ".join(sys.argv[1:]) or "¿Cuánto tiempo hay para reconocer una alerta SEV1?"
    print(asyncio.run(get_rag_response(pregunta)).model_dump_json(indent=2))
