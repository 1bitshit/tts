"""
LM Studio OpenAI-compatible API client for debate LLM inference.
LM Studio runs at http://localhost:1234/v1 by default.
"""
import json
import logging
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
        if model:
            payload["model"] = model

        resp = await self._client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
        )
        resp.raise_for_status()
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
        if model:
            payload["model"] = model

        async with self._client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=payload,
        ) as resp:
            resp.raise_for_status()
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

    async def is_healthy(self) -> bool:
        try:
            await self.ensure_client()
            resp = await self._client.get(f"{self.base_url}/models", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False


_lm_studio_client: Optional[LMStudioClient] = None


def get_lm_studio_client() -> LMStudioClient:
    global _lm_studio_client
    if _lm_studio_client is None:
        _lm_studio_client = LMStudioClient()
    return _lm_studio_client
