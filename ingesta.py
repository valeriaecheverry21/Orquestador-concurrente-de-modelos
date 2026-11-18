"""Módulo de ingesta: lee los documentos de ./data, los fragmenta y los guarda en ChromaDB (./vectorstore).

Uso:
    python ingesta.py              # indexa solo si la base no existe o si los documentos cambiaron
    python ingesta.py --rebuild    # fuerza la reindexación completa

Decisiones:
- Embeddings locales (HuggingFace `intfloat/multilingual-e5-small`): multilingüe, gratis y sin API key.
  El MISMO objeto de embeddings (`get_embeddings()`) se usa para indexar y para consultar, y el nombre
  del modelo se guarda en los metadatos de la colección para detectar cualquier desajuste.
- Chunking: RecursiveCharacterTextSplitter con separadores de Markdown, 500 tokens y 50 de overlap.
  Los tokens se cuentan con el tokenizador del propio modelo de embeddings, así que ningún fragmento
  supera su ventana de 512 tokens (no se trunca nada al generar el embedding).
- Persistencia: se guarda una huella (hash) de los documentos y de la configuración. Si la huella de la
  colección coincide con la actual, no se vuelve a indexar.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

logger = logging.getLogger("ingesta")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
VECTORSTORE_DIR = BASE_DIR / "vectorstore"
COLLECTION_NAME = "manual_atlas"
EXTENSIONES = (".md", ".txt")

EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
# Los modelos E5 esperan estos prefijos para distinguir documentos de consultas.
PREFIJO_DOCUMENTO = "passage: "
PREFIJO_CONSULTA = "query: "

CHUNK_SIZE = 500  # tokens
CHUNK_OVERLAP = 50  # tokens


@dataclass(frozen=True)
class ResultadoIngesta:
    documentos: int
    fragmentos: int
    reindexado: bool


@lru_cache(maxsize=1)
def get_embeddings() -> Embeddings:
    """Modelo de embeddings único para indexar y consultar (se carga una sola vez)."""
    from langchain_huggingface import HuggingFaceEmbeddings

    logger.info("Cargando modelo de embeddings %s", EMBEDDING_MODEL)
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        encode_kwargs={"normalize_embeddings": True, "prompt": PREFIJO_DOCUMENTO},
        query_encode_kwargs={"normalize_embeddings": True, "prompt": PREFIJO_CONSULTA},
    )


def build_splitter() -> RecursiveCharacterTextSplitter:
    """Splitter que mide los fragmentos en tokens del modelo de embeddings."""
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL)
    return RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
        tokenizer,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=RecursiveCharacterTextSplitter.get_separators_for_language(Language.MARKDOWN),
    )


def cargar_documentos(data_dir: Path = DATA_DIR) -> list[Document]:
    """Lee los .md / .txt de la carpeta, en orden alfabético."""
    archivos = sorted(p for p in data_dir.iterdir() if p.suffix.lower() in EXTENSIONES)
    if not archivos:
        raise FileNotFoundError(f"No hay archivos {EXTENSIONES} en {data_dir}")
    return [Document(page_content=p.read_text(encoding="utf-8"), metadata={"fuente": p.name}) for p in archivos]


def fragmentar(documentos: list[Document], splitter: RecursiveCharacterTextSplitter) -> list[Document]:
    """Divide cada documento y asigna un id estable `<archivo>#<n>` a cada fragmento."""
    fragmentos: list[Document] = []
    for doc in documentos:
        for i, texto in enumerate(splitter.split_text(doc.page_content)):
            fuente = doc.metadata["fuente"]
            fragmentos.append(
                Document(id=f"{fuente}#{i}", page_content=texto, metadata={"fuente": fuente, "fragmento": i})
            )
    return fragmentos


def huella(documentos: list[Document]) -> str:
    """Hash de los documentos y de la configuración: si cambia cualquiera de los dos, hay que reindexar."""
    h = hashlib.sha256(f"{EMBEDDING_MODEL}|{CHUNK_SIZE}|{CHUNK_OVERLAP}".encode())
    for doc in documentos:
        h.update(doc.metadata["fuente"].encode())
        h.update(doc.page_content.encode())
    return h.hexdigest()


_lock_clientes = threading.Lock()


@lru_cache(maxsize=None)
def _cliente_cacheado(ruta: str) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=ruta)


def _cliente(persist_dir: Path) -> chromadb.ClientAPI:
    """Un único cliente por carpeta: chromadb falla si se crean dos a la vez sobre la misma ruta."""
    with _lock_clientes:
        return _cliente_cacheado(str(Path(persist_dir).resolve()))


def abrir_vectorstore(
    embeddings: Embeddings | None = None,
    persist_dir: Path = VECTORSTORE_DIR,
    collection_name: str = COLLECTION_NAME,
) -> Chroma:
    """Abre la colección existente comprobando que se indexó con el mismo modelo de embeddings."""
    cliente = _cliente(persist_dir)
    try:
        coleccion = cliente.get_collection(collection_name)
    except Exception as exc:  # chromadb lanza NotFoundError (o ValueError en versiones antiguas)
        raise FileNotFoundError(
            f"La colección '{collection_name}' no existe en {persist_dir}. Ejecuta primero: python ingesta.py"
        ) from exc
    metadatos = coleccion.metadata or {}
    if "huella" not in metadatos:
        raise FileNotFoundError("La indexación anterior quedó incompleta. Ejecuta: python ingesta.py --rebuild")
    modelo_indexado = metadatos.get("embedding_model")
    if modelo_indexado != EMBEDDING_MODEL:
        raise RuntimeError(
            f"La colección se indexó con '{modelo_indexado}' pero se consulta con '{EMBEDDING_MODEL}'. "
            "Las distancias no serían comparables: ejecuta python ingesta.py --rebuild"
        )
    return Chroma(client=cliente, collection_name=collection_name, embedding_function=embeddings or get_embeddings())


def ingest(
    data_dir: Path = DATA_DIR,
    persist_dir: Path = VECTORSTORE_DIR,
    collection_name: str = COLLECTION_NAME,
    *,
    rebuild: bool = False,
    embeddings: Embeddings | None = None,
    splitter: RecursiveCharacterTextSplitter | None = None,
) -> ResultadoIngesta:
    """Indexa los documentos de `data_dir` en ChromaDB, salvo que la colección ya esté al día."""
    documentos = cargar_documentos(data_dir)
    huella_actual = huella(documentos)
    cliente = _cliente(persist_dir)

    existente = next((c for c in cliente.list_collections() if c.name == collection_name), None)
    if existente is not None:
        coleccion = cliente.get_collection(collection_name)
        metadatos = coleccion.metadata or {}
        if not rebuild and metadatos.get("huella") == huella_actual:
            logger.info("La colección '%s' ya está al día (%d fragmentos): no se reindexa",
                        collection_name, coleccion.count())
            return ResultadoIngesta(len(documentos), coleccion.count(), reindexado=False)
        motivo = "--rebuild" if rebuild else "los documentos o la configuración cambiaron"
        logger.info("Se reconstruye la colección '%s' (%s)", collection_name, motivo)
        cliente.delete_collection(collection_name)

    fragmentos = fragmentar(documentos, splitter or build_splitter())
    logger.info("%d documentos -> %d fragmentos (chunk_size=%d tokens, overlap=%d)",
                len(documentos), len(fragmentos), CHUNK_SIZE, CHUNK_OVERLAP)

    vectorstore = Chroma(
        client=cliente,
        collection_name=collection_name,
        embedding_function=embeddings or get_embeddings(),
        collection_configuration={"hnsw": {"space": "cosine"}},
    )
    vectorstore.add_documents(fragmentos, ids=[f.id for f in fragmentos])

    # La huella se escribe al final: si la indexación se interrumpe, la próxima ejecución la rehace.
    vectorstore._collection.modify(
        metadata={"huella": huella_actual, "embedding_model": EMBEDDING_MODEL,
                  "chunk_size": CHUNK_SIZE, "chunk_overlap": CHUNK_OVERLAP}
    )
    logger.info("Colección '%s' guardada en %s", collection_name, persist_dir)
    return ResultadoIngesta(len(documentos), len(fragmentos), reindexado=True)


def configurar_logs() -> None:
    """Logs en UTF-8 (consola de Windows) y sin el ruido de las librerías de descarga/HTTP."""
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    for ruidoso in ("httpx", "huggingface_hub", "sentence_transformers", "transformers", "chromadb"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexa ./data en ChromaDB")
    parser.add_argument("--rebuild", action="store_true", help="fuerza la reindexación completa")
    args = parser.parse_args()

    configurar_logs()
    resultado = ingest(rebuild=args.rebuild)
    estado = "indexados" if resultado.reindexado else "ya estaban indexados"
    print(f"{resultado.documentos} documentos / {resultado.fragmentos} fragmentos {estado} en {VECTORSTORE_DIR}")


if __name__ == "__main__":
    main()
