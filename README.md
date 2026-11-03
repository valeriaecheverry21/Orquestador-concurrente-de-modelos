# Unified Async LLM Client

Cliente asíncrono en Python 3.12 que ofrece **una única interfaz** para OpenAI y Anthropic (Claude), con streaming, validación con Pydantic, reintentos y manejo controlado de errores.

## Estructura

| Archivo | Contenido |
|---|---|
| `schemas.py` | Modelos Pydantic: `ChatMessage`, `ModelConfig` (temperatura 0–2, `max_tokens`, timeout, reintentos), `ModelResponse`, `StreamChunk`, `LLMError` |
| `llm_clients.py` | `BaseLLMClient` (clase abstracta), `OpenAIClient`, `AnthropicClient` y `AsyncLLMManager` |
| `main.py` | Script de validación: pregunta "¿Qué es la entropía?" en modo normal y en streaming |
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
