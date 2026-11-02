# Orquestador concurrente de modelos

Simulación en Python (asyncio) de un backend que consulta varios modelos de IA a la vez.

- Tres corrutinas (`gpt_4_call`, `claude_3_call`, `local_llama_call`) simulan latencia con `asyncio.sleep`.
- `asyncio.gather` lanza las llamadas en paralelo.
- `asyncio.timeout(2)` limita la ejecución total; el `TimeoutError` se captura sin detener el programa.
- `asyncio.Semaphore(2)` limita a 2 llamadas simultáneas en una ráfaga de 10.

## Ejecutar

Requiere Python 3.12+ (sin dependencias externas).

```
python -m venv .venv
.venv\Scripts\python.exe orquestador.py
```
