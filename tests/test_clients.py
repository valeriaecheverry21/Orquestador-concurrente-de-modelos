"""Pruebas sin red: se simula la API con un transporte HTTP falso.

Ejecutar:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import httpx2
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm_clients import AsyncLLMManager  # noqa: E402
from schemas import ChatMessage, ErrorType, ModelConfig, Role  # noqa: E402


def sse(events: list[tuple[str | None, dict | str]]) -> bytes:
    out = []
    for name, data in events:
        payload = data if isinstance(data, str) else json.dumps(data)
        out.append((f"event: {name}\n" if name else "") + f"data: {payload}\n\n")
    return "".join(out).encode()


def openai_ok(request: httpx2.Request) -> httpx2.Response:
    body = json.loads(request.content)
    if body.get("stream"):
        chunks = [
            (None, {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "gpt-test",
                    "choices": [{"index": 0, "delta": {"content": t}, "finish_reason": None}]})
            for t in ["La ", "entropía ", "mide el desorden."]
        ] + [(None, "[DONE]")]
        return httpx2.Response(200, content=sse(chunks), headers={"content-type": "text/event-stream"})
    return httpx2.Response(200, json={
        "id": "c", "object": "chat.completion", "created": 0, "model": "gpt-test",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "La entropía mide el desorden."}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
    })


def anthropic_ok(request: httpx2.Request) -> httpx2.Response:
    body = json.loads(request.content)
    assert "temperature" not in body, "Anthropic no debe recibir temperature"
    assert all(m["role"] != "system" for m in body["messages"])
    usage = {"input_tokens": 12, "output_tokens": 8}
    if body.get("stream"):
        msg = {"id": "m", "type": "message", "role": "assistant", "model": "claude-test", "content": [],
               "stop_reason": None, "stop_sequence": None, "usage": usage}
        events = [("message_start", {"type": "message_start", "message": msg}),
                  ("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "text", "text": ""}})]
        events += [("content_block_delta", {"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "text_delta", "text": t}})
                   for t in ["La ", "entropía ", "mide el desorden."]]
        events += [("content_block_stop", {"type": "content_block_stop", "index": 0}),
                   ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn",
                                      "stop_sequence": None}, "usage": {"output_tokens": 8}}),
                   ("message_stop", {"type": "message_stop"})]
        return httpx2.Response(200, content=sse(events), headers={"content-type": "text/event-stream"})
    return httpx2.Response(200, json={
        "id": "m", "type": "message", "role": "assistant", "model": "claude-test",
        "content": [{"type": "thinking", "thinking": "", "signature": "x"},
                    {"type": "text", "text": "La entropía mide el desorden."}],
        "stop_reason": "end_turn", "stop_sequence": None, "usage": usage,
    })


def error_handler(status: int, body: dict, counter: list[int] | None = None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if counter is not None:
            counter.append(1)
        return httpx2.Response(status, json=body)
    return handler


def make(provider: str, handler, **cfg) -> AsyncLLMManager:
    config = ModelConfig(provider=provider, retry_base_delay=0, **cfg)
    return AsyncLLMManager(config, api_key="test-key",
                           http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


MSGS = [ChatMessage(role=Role.SYSTEM, content="Sé breve."), ChatMessage(role=Role.USER, content="¿Qué es la entropía?")]
OK = {"openai": openai_ok, "anthropic": anthropic_ok}


class SchemaTests(unittest.TestCase):
    def test_temperatura_fuera_de_rango(self):
        for t in (-0.1, 2.1):
            with self.assertRaises(ValidationError):
                ModelConfig(provider="openai", temperature=t)

    def test_max_tokens_y_proveedor(self):
        with self.assertRaises(ValidationError):
            ModelConfig(provider="openai", max_tokens=0)
        with self.assertRaises(ValidationError):
            ModelConfig(provider="gemini")

    def test_mensaje_vacio(self):
        with self.assertRaises(ValidationError):
            ChatMessage(role=Role.USER, content="   ")


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_ok(self):
        for provider, handler in OK.items():
            with self.subTest(provider=provider):
                async with make(provider, handler) as m:
                    r = await m.generate(MSGS)
                self.assertTrue(r.ok, r.error)
                self.assertEqual(r.content, "La entropía mide el desorden.")
                self.assertGreater(r.usage.total_tokens, 0)

    async def test_stream_ok(self):
        for provider, handler in OK.items():
            with self.subTest(provider=provider):
                async with make(provider, handler) as m:
                    chunks = [c async for c in m.stream(MSGS)]
                self.assertEqual([c.text for c in chunks], ["La ", "entropía ", "mide el desorden."])
                self.assertTrue(all(c.error is None for c in chunks))

    async def test_rate_limit_reintenta_y_no_rompe(self):
        for provider in OK:
            with self.subTest(provider=provider):
                calls: list[int] = []
                body = {"error": {"type": "rate_limit_error", "message": "Too many requests"}}
                async with make(provider, error_handler(429, body, calls), max_retries=2) as m:
                    r = await m.generate("hola")
                self.assertFalse(r.ok)
                self.assertEqual(r.error.type, ErrorType.RATE_LIMIT)
                self.assertEqual(len(calls), 3)  # 1 intento + 2 reintentos
                self.assertEqual(r.attempts, 3)

    async def test_rate_limit_y_luego_ok(self):
        calls: list[int] = []

        def flaky(request):
            calls.append(1)
            return httpx2.Response(429, json={"error": {"message": "slow down"}}) if len(calls) == 1 else openai_ok(request)

        async with make("openai", flaky) as m:
            r = await m.generate("hola")
        self.assertTrue(r.ok)
        self.assertEqual(r.attempts, 2)

    async def test_api_key_invalida_no_reintenta(self):
        for provider in OK:
            with self.subTest(provider=provider):
                calls: list[int] = []
                body = {"error": {"type": "authentication_error", "message": "invalid x-api-key"}}
                async with make(provider, error_handler(401, body, calls)) as m:
                    r = await m.generate("hola")
                    chunks = [c async for c in m.stream("hola")]
                self.assertEqual(r.error.type, ErrorType.AUTHENTICATION)
                self.assertEqual(len(calls), 2)  # una vez generate + una vez stream, sin reintentos
                self.assertEqual(len(chunks), 1)
                self.assertEqual(chunks[0].error.type, ErrorType.AUTHENTICATION)

    async def test_cuota_agotada_openai(self):
        body = {"error": {"message": "You exceeded your quota", "type": "insufficient_quota", "code": "insufficient_quota"}}
        async with make("openai", error_handler(429, body)) as m:
            r = await m.generate("hola")
        self.assertEqual(r.error.type, ErrorType.QUOTA)
        self.assertEqual(r.attempts, 1)

    async def test_error_de_red(self):
        def boom(request):
            raise httpx2.ConnectError("sin conexión", request=request)

        for provider in OK:
            with self.subTest(provider=provider):
                async with make(provider, boom, max_retries=1) as m:
                    r = await m.generate("hola")
                    chunks = [c async for c in m.stream("hola")]
                self.assertEqual(r.error.type, ErrorType.CONNECTION)
                self.assertEqual(chunks[-1].error.type, ErrorType.CONNECTION)

    async def test_falta_api_key(self):
        m = AsyncLLMManager(ModelConfig(provider="anthropic"), api_key="")
        m.client._sdk = None  # simula que no hay clave en el entorno
        r = await m.generate("hola")
        self.assertEqual(r.error.type, ErrorType.CONFIG)

    async def test_llamadas_concurrentes(self):
        import asyncio

        async with make("openai", openai_ok) as m:
            results = await asyncio.gather(*(m.generate(f"pregunta {i}") for i in range(5)))
        self.assertTrue(all(r.ok for r in results))


if __name__ == "__main__":
    unittest.main()
