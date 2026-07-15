"""
LM Studio OpenAI-compatible API client for debate LLM inference.
LM Studio runs at http://localhost:1234/v1 by default.
"""
import json
import logging
import re
import httpx
from typing import AsyncGenerator, List, Optional
from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120.0


class LMStudioClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = (base_url or self.default_base_url()).rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @staticmethod
    def default_base_url() -> str:
        return (
            f"http://{settings.lm_studio_internal_host}:"
            f"{settings.lm_studio_internal_port}/v1"
        )

    async def ensure_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def chat_completion(
        self,
        messages: List[dict],
        model: str = "",
        temperature: float = 0.8,
        max_tokens: int = 512,
        stream: bool = False,
    ) -> dict:
        await self.ensure_client()
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        payload["model"] = await self.resolve_chat_model(model)

        resp = await self._client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
        )
        if resp.is_error:
            raise RuntimeError(
                f"LM Studio {resp.status_code}: {resp.text[:1200]} "
                f"(model={payload['model']})"
            )
        return resp.json()

    async def chat_completion_stream(
        self,
        messages: List[dict],
        model: str = "",
        temperature: float = 0.8,
        max_tokens: int = 512,
    ) -> AsyncGenerator[str, None]:
        await self.ensure_client()
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        payload["model"] = await self.resolve_chat_model(model)

        async with self._client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=payload,
        ) as resp:
            if resp.is_error:
                body = (await resp.aread()).decode(errors="replace")
                raise RuntimeError(f"LM Studio {resp.status_code}: {body[:1200]}")
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

    async def list_models(self) -> List[str]:
        await self.ensure_client()
        resp = await self._client.get(f"{self.base_url}/models")
        resp.raise_for_status()
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]

    async def list_chat_models(self) -> List[str]:
        await self.ensure_client()
        root = self.base_url.removesuffix("/v1")
        resp = await self._client.get(f"{root}/api/v1/models")
        resp.raise_for_status()
        return [
            model.get("key") or model.get("modelKey")
            for model in resp.json().get("models", [])
            if model.get("type") == "llm" and (model.get("key") or model.get("modelKey"))
        ]

    async def resolve_chat_model(self, requested: str = "") -> str:
        models = await self.list_chat_models()
        if not models:
            raise RuntimeError(
                "Kein Chat-LLM in LM Studio installiert. Öffne Einstellungen → "
                "Tägliches Modelllabor und installiere Qwen3-0.6B oder Qwen3-1.7B."
            )
        if requested:
            requested_normalized = re.sub(
                r"[^a-z0-9]", "", requested.lower().removesuffix("-gguf")
            )
            for model in models:
                model_normalized = re.sub(r"[^a-z0-9]", "", model.lower())
                if (
                    model.lower() == requested.lower()
                    or model_normalized in requested_normalized
                    or requested_normalized in model_normalized
                ):
                    return model
            raise RuntimeError(
                f"Modell '{requested}' ist nicht in LM Studio installiert. "
                f"Verfügbar: {', '.join(models)}"
            )
        return models[0]

    async def is_healthy(self) -> bool:
        try:
            return bool(await self.list_chat_models())
        except Exception:
            return False


_lm_studio_client: Optional[LMStudioClient] = None


def get_lm_studio_client() -> LMStudioClient:
    global _lm_studio_client
    if _lm_studio_client is None:
        _lm_studio_client = LMStudioClient()
    return _lm_studio_client
