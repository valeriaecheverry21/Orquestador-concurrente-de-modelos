# Unified Async LLM Client

Cliente asíncrono en Python 3.12 que ofrece **una única interfaz** para OpenAI y Anthropic (Claude), con streaming, validación con Pydantic, reintentos y manejo controlado de errores.

## Estructura

| Archivo | Contenido |
|---|---|
| `schemas.py` | Modelos Pydantic: `ChatMessage`, `ModelConfig` (temperatura 0–2, `max_tokens`, timeout, reintentos), `ModelResponse`, `StreamChunk`, `LLMError` |
| `llm_clients.py` | `BaseLLMClient` (clase abstracta), `OpenAIClient`, `AnthropicClient` y `AsyncLLMManager` |
| `main.py` | Script de validación: pregunta "¿Qué es la entropía?" en modo normal y en streaming |
| `server.py` + `static/index.html` | Servidor web local (FastAPI) con una página para probar el cliente |
| `tests/test_clients.py` | Pruebas sin red (API simulada): respuestas, streaming, rate limit, API key inválida, errores de red |
| `.env.example` | Plantilla de variables de entorno |
| `orquestador.py` | Ejercicio anterior (simulación de llamadas concurrentes con `asyncio`) |

## Instalación

Requiere Python 3.12.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## Variables de entorno

Copia la plantilla y completa tus claves:

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

| Variable | Obligatoria | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | Para usar OpenAI | Clave de https://platform.openai.com/api-keys |
| `ANTHROPIC_API_KEY` | Para usar Anthropic | Clave de https://console.anthropic.com/ |
| `LLM_PROVIDER` | No (por defecto `all` en `main.py`) | `openai`, `anthropic` o `all` |
| `OPENAI_MODEL` | No (por defecto `gpt-4o-mini`) | Modelo de OpenAI |
| `ANTHROPIC_MODEL` | No (por defecto `claude-opus-5-5`) | Modelo de Claude |
| `LLM_TEMPERATURE` | No (por defecto `0.7`) | Entre 0 y 2. Solo se envía a OpenAI (ver notas) |
| `LLM_MAX_TOKENS` | No (por defecto `4096`) | Máximo de tokens de salida |
| `LLM_TIMEOUT` | No (por defecto `60`) | Segundos por petición |
| `LLM_MAX_RETRIES` | No (por defecto `3`) | Reintentos ante errores transitorios |

El archivo `.env` está en `.gitignore`: nunca se sube al repositorio.

## Ejecutar el script de prueba

```bash
python main.py                       # ambos proveedores
python main.py --provider openai
python main.py --provider anthropic
```

Para cada proveedor se hace la misma pregunta dos veces: primero en modo normal (respuesta completa, latencia y tokens) y después en streaming (el texto aparece a medida que llega). Si falta una clave o es inválida, se muestra un error controlado y el script sigue con el siguiente proveedor.

## Interfaz web en localhost (opcional)

```bash
uvicorn server:app
```

Abre http://localhost:8000, elige el proveedor, escribe la pregunta y pulsa **Enviar (streaming)** o **Enviar (normal)**. Para detener el servidor, pulsa `Ctrl+C` en la terminal.

Endpoints que expone `server.py` (documentación interactiva en http://localhost:8000/docs):

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/providers` | Proveedores disponibles y si tienen API key |
| `POST` | `/api/chat` | Respuesta completa (`ModelResponse`) |
| `POST` | `/api/chat/stream` | Streaming: una línea JSON (`StreamChunk`) por fragmento |

Cuerpo de las peticiones POST: `{"provider": "openai" | "anthropic", "message": "..."}`.

## Pruebas sin API key

```bash
python -m unittest discover -s tests -v
```

Simulan las respuestas HTTP de ambas APIs, así que no hacen llamadas reales ni gastan saldo.

## Uso desde código

```python
import asyncio
from dotenv import load_dotenv
from llm_clients import AsyncLLMManager
from schemas import ChatMessage, ModelConfig, Role

async def demo():
    load_dotenv()
    config = ModelConfig(provider="anthropic", max_tokens=1024)   # o provider="openai"
    async with AsyncLLMManager(config) as llm:
        # Modo normal
        resp = await llm.generate("¿Qué es la entropía?")
        print(resp.content if resp.ok else resp.error)

        # Streaming
        async for chunk in llm.stream([ChatMessage(role=Role.USER, content="Explícalo en una frase")]):
            print(chunk.error or chunk.text, end="", flush=True)

asyncio.run(demo())
```

`AsyncLLMManager.from_env()` arma la configuración a partir de las variables de entorno. Para cambiar de proveedor solo hay que cambiar `provider`; el resto del código no cambia.

## Decisiones de diseño

- **Sin bloquear el event loop**: se usan `AsyncOpenAI` y `AsyncAnthropic`, y todas las llamadas usan `await`.
- **Streaming**: `stream()` es un generador asíncrono (`async for` + `yield`) que emite `StreamChunk` con cada fragmento de texto.
- **Errores controlados**: `generate()` y `stream()` no lanzan excepciones de la API. Las convierten en un `LLMError` con tipo (`authentication`, `rate_limit`, `quota`, `timeout`, `connection`, `server`, ...) y un mensaje.
- **Reintentos con backoff exponencial**: solo para errores transitorios (rate limit 429, timeouts, errores de red, 5xx). Una API key inválida o la cuota agotada no se reintentan, porque fallarían igual. En streaming solo se reintenta si todavía no llegó ningún fragmento, para no duplicar texto.
- **Validación**: Pydantic rechaza configuraciones inválidas (`temperature=3`, `max_tokens=0`, proveedor desconocido, mensajes vacíos) antes de llamar a la API.

## Notas sobre Anthropic

- Los modelos actuales de Claude **no aceptan `temperature`** (la API responde 400). El parámetro se valida igual en `ModelConfig` para mantener la interfaz común, pero solo se envía a OpenAI.
- Los mensajes con rol `system` se envían en el parámetro `system`, que es como lo espera la API de Anthropic.
- Está activado el *fallback* del servidor (`fallbacks="default"`): si el modelo rechaza una petición por sus filtros de seguridad, la API la reintenta automáticamente con otro modelo dentro de la misma llamada.

---

# Pre-entrega 2: Pipeline de extracción de entidades técnicas

Recibe un párrafo de texto sin procesar (una descripción de arquitectura, un log de error...) y devuelve un objeto validado con Pydantic, usando LangChain (LCEL).

| Archivo | Contenido |
|---|---|
| `schemas.py` | `EntidadesTecnicas` (contrato de salida) y el enum `NivelCriticidad` (`baja`, `media`, `alta`) |
| `chain.py` | `ChatPromptTemplate`, cadena LCEL con `.with_structured_output()` y `.with_retry()`, y la función `process_text()` |
| `test_pipeline.py` | Mini-script asíncrono de prueba (usa la API real) |
| `tests/test_chain.py` | Pruebas sin red: esquema, recuperación tras un JSON mal formado y respuesta cortada |

## La cadena

```python
chain = PROMPT | model.with_structured_output(EntidadesTecnicas, include_raw=True) | RunnableLambda(_validar)
chain = chain.with_retry(retry_if_exception_type=ERRORES_REINTENTABLES, stop_after_attempt=3, wait_exponential_jitter=True)
```

- **Contrato (`EntidadesTecnicas`)**: `tecnologias` no puede estar vacía (se quitan duplicados y nombres en blanco), `nivel_de_criticidad` solo admite los tres valores del enum y `resumen_tecnico` tiene entre 10 y 400 caracteres. No se aceptan campos extra.
- **Prompt modular**: `ChatPromptTemplate` con las variables `{texto}` y `{instrucciones_formato}`; no hay f-strings.
- **`finish_reason`**: con `include_raw=True` la cadena recibe también el mensaje crudo del modelo. Si el `finish_reason` / `stop_reason` indica que la respuesta se cortó (`length`, `max_tokens`), el objeto se descarta aunque se haya podido parsear y se lanza `RespuestaIncompletaError`.
- **Reintentos**: un JSON mal formado, una salida que no cumple el esquema o una respuesta cortada lanzan `SalidaInvalidaError`, y `.with_retry()` repite la llamada (3 intentos en total, con backoff exponencial). Los errores de red y de rate limit los reintenta el SDK (`LLM_MAX_RETRIES`).
- **Modelo**: `build_model()` reutiliza `ModelConfig` y las variables de entorno del Módulo 1 (`LLM_PROVIDER`, `OPENAI_MODEL`, `ANTHROPIC_MODEL`, `LLM_MAX_TOKENS`...) para crear un `ChatOpenAI` o un `ChatAnthropic`.
- **Logs**: el logger `pipeline` registra cada respuesta recibida, su `finish_reason`, el resultado de la validación y cada intento fallido.

## Cómo ejecutarlo

```bash
pip install -r requirements.txt
python test_pipeline.py                              # usa el proveedor de LLM_PROVIDER
python -m unittest tests.test_chain -v               # pruebas sin red, no gastan saldo
```

`test_pipeline.py` ejecuta tres casos: un texto claro, un texto ambiguo (prueba de estrés) y una llamada con `max_tokens=15` para forzar una respuesta cortada y ver los reintentos en los logs.

Uso desde código:

```python
import asyncio
from chain import process_text

resultado = asyncio.run(process_text("La API en FastAPI agota el pool de conexiones de PostgreSQL..."))
print(resultado.model_dump_json(indent=2))
```

## Ejemplo de salida

Formato del objeto que devuelve `process_text()` (ejemplo ilustrativo):

```json
{
  "tecnologias": ["FastAPI", "Redis", "PostgreSQL"],
  "nivel_de_criticidad": "alta",
  "resumen_tecnico": "API con caché en Redis y persistencia en PostgreSQL; cuello de botella en conexiones concurrentes."
}
```

---

# Pre-entrega 3: Sistema de recuperación semántica local (RAG)

Responde preguntas sobre un manual técnico usando **solo** la información de los documentos indexados. Si la respuesta no está en ellos, dice **"No lo sé"** en lugar de inventarla.

| Archivo | Contenido |
|---|---|
| `data/` | Dataset de ejemplo: 4 capítulos (`.md`) del manual de **Atlas**, una plataforma ficticia de seguimiento de envíos (arquitectura, despliegue, incidentes, seguridad) |
| `ingesta.py` | Módulo de ingesta: carga, chunking, embeddings y persistencia en ChromaDB (`./vectorstore`) |
| `rag_chain.py` | Retriever, prompt de "filtro de veracidad", cadena LCEL y `get_rag_response()` asíncrona |
| `schemas.py` | `RespuestaGenerada` (lo que parsea el `PydanticOutputParser`) y `RespuestaRAG` / `Referencia` (salida final) |
| `test_rag.py` | Las dos pruebas end-to-end con la API real: pregunta con respuesta y pregunta trampa |
| `tests/test_rag_chain.py` | Pruebas sin red: ingesta, persistencia, chunking, referencias, "No lo sé" y reintentos |

El dataset es ficticio a propósito: como el modelo no puede conocer Atlas de antemano, cualquier dato correcto en la respuesta tiene que venir del contexto recuperado.

## Cómo ejecutarlo

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux / macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # Linux / macOS: cp .env.example .env  → completar ANTHROPIC_API_KEY (u OPENAI_API_KEY)

python ingesta.py                 # 1) indexa ./data en ./vectorstore (la 1.ª vez descarga el modelo de embeddings, ~470 MB)
python test_rag.py                # 2) pregunta con respuesta + pregunta trampa, con verificaciones automáticas
python rag_chain.py "¿Cuándo se puede desplegar a producción?"   # pregunta libre

python -m unittest tests.test_rag_chain -v                     # pruebas sin red: no gastan saldo
```

`python ingesta.py` se puede ejecutar todas las veces que se quiera: si los documentos no cambiaron, no reindexa nada. `python ingesta.py --rebuild` fuerza la reindexación. `get_rag_response()` también llama a la ingesta, así que el paso 1 es opcional.

## Flujo

```
pregunta ─► RecuperadorSemantico: embedding de la pregunta ─► top-4 en ChromaDB (similitud coseno)
         ─► fragmentos en <fragmento id="..."> ─► PROMPT ─► LLM (async) ─► PydanticOutputParser
         ─► validación de referencias ─► RespuestaRAG {respuesta, encontrada, referencias[]}
```

```python
import asyncio
from rag_chain import get_rag_response

r = asyncio.run(get_rag_response("¿En cuánto tiempo hay que reconocer una alerta SEV1?"))
print(r.respuesta, [ref.id for ref in r.referencias])
```

## Decisiones de diseño

- **Embeddings locales e iguales en ambos lados**: `intfloat/multilingual-e5-small` (HuggingFace, multilingüe, sin API key). `get_embeddings()` es la única fuente del modelo, tanto para indexar como para consultar. Además, el nombre del modelo se guarda en los metadatos de la colección y `abrir_vectorstore()` se niega a consultar si no coincide (evita el error de los embeddings no coincidentes). Los prefijos `passage:` / `query:` que pide E5 se aplican automáticamente.
- **Chunking**: `RecursiveCharacterTextSplitter` con separadores de Markdown (corta primero por títulos, luego por párrafos), **500 tokens y 50 de overlap**. Los tokens se cuentan con el tokenizador del propio modelo de embeddings, cuyo límite es de 512 tokens: así ningún fragmento se trunca al vectorizarlo. El dataset produce 11 fragmentos.
- **Persistencia**: `chromadb.PersistentClient` en `./vectorstore` (ignorada por git). La colección guarda una huella SHA-256 de los documentos y de la configuración (modelo, chunk size, overlap). Si la huella coincide, la ingesta no hace nada; si cambia un documento, se reconstruye. La huella se escribe al final, así que una indexación interrumpida se rehace en la siguiente ejecución.
- **Retriever**: `RecuperadorSemantico` (un `BaseRetriever` de LangChain) calcula el embedding de la pregunta y busca por vector. Devuelve **top_k = 4** fragmentos con su `id` (`archivo#n`) y su similitud. La búsqueda de Chroma es síncrona y se ejecuta en un hilo (`asyncio.to_thread`) para no bloquear el event loop.
- **Generación grounded**: el prompt de sistema actúa como filtro de veracidad: solo puede usar el CONTEXTO y, si la respuesta no está, debe empezar con "No lo sé", con `encontrada=false` y sin referencias. La salida pasa por un `PydanticOutputParser` cuyas instrucciones de formato van en el prompt.
- **Referencias verificadas**: las referencias que da el modelo se cruzan con los fragmentos realmente recuperados. Las inventadas se descartan y se completan las válidas con fuente, similitud y extracto. Si el modelo dice que encontró la respuesta pero no cita ningún fragmento real, la salida se considera inválida.
- **Reintentos**: un JSON mal formado o referencias inválidas reintentan **solo la generación** (3 intentos con backoff); la búsqueda no se repite.
- **LLM**: se reutiliza `build_model()` de la pre-entrega 2 (`LLM_PROVIDER`, `ANTHROPIC_MODEL`, `OPENAI_MODEL`... del `.env`).
- **Concurrencia**: `test_rag.py` lanza las dos preguntas a la vez con `asyncio.gather`. El modelo de embeddings, el cliente de Chroma y la cadena se cargan una sola vez, protegidos con un lock.

## Las dos pruebas (`test_rag.py`)

| Prueba | Pregunta | Resultado esperado |
|---|---|---|
| Respuesta en los documentos | ¿En cuánto tiempo hay que reconocer una alerta SEV1 y en qué plazo debe publicarse el postmortem? | "5 minutos" y "5 días hábiles", citando `03_gestion_de_incidentes.md` |
| Pregunta trampa | ¿Qué proveedor de SMS usa atlas-notify para enviar los mensajes a los clientes? | "No lo sé…", `encontrada=false`, sin referencias. El manual dice que atlas-notify envía SMS, pero nunca nombra al proveedor, así que una respuesta concreta sería una alucinación |

El script imprime ambas respuestas en JSON y una lista de verificaciones `[OK]` / `[FALLO]`.
