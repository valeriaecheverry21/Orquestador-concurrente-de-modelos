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
