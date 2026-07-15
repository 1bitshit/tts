"""Authenticated reverse proxy for the local-only LM Studio server."""

import hmac
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.config import settings

_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _client
    _client = httpx.AsyncClient(timeout=None)
    yield
    await _client.aclose()
    _client = None


app = FastAPI(
    title="Qwen TTS LM Studio Proxy",
    description="LM Studio proxy protected by the same API_KEYS as Qwen TTS",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def _request_api_key(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    return None


def _authorize(request: Request) -> None:
    valid_keys = settings.get_api_keys_list()
    if not valid_keys:
        if settings.env == "production":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Security Error: No API keys configured in production environment.",
            )
        return

    supplied_key = _request_api_key(request)
    if not supplied_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Use X-API-Key or Authorization: Bearer <key>.",
        )
    if not any(hmac.compare_digest(supplied_key, key) for key in valid_keys):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")


async def _close_upstream(response: httpx.Response) -> None:
    await response.aclose()


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(path: str, request: Request):
    _authorize(request)
    if _client is None:
        raise HTTPException(status_code=503, detail="LM proxy is starting")

    upstream = (
        f"http://{settings.lm_studio_internal_host}:"
        f"{settings.lm_studio_internal_port}/{path}"
    )
    excluded = {"host", "content-length", "authorization", "x-api-key"}
    headers = {key: value for key, value in request.headers.items() if key.lower() not in excluded}
    body = await request.body()

    try:
        upstream_request = _client.build_request(
            request.method,
            upstream,
            params=request.query_params,
            headers=headers,
            content=body,
        )
        response = await _client.send(upstream_request, stream=True)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"LM Studio unavailable: {exc}") from exc

    response_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-encoding", "transfer-encoding", "connection"}
    }
    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers=response_headers,
        background=BackgroundTask(_close_upstream, response),
    )
