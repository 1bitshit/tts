from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.lms_proxy import _authorize


def request_with_headers(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/models",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        }
    )


def test_proxy_accepts_same_x_api_key_as_tts(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "tts-key,second-key")
    _authorize(request_with_headers({"X-API-Key": "tts-key"}))


def test_proxy_accepts_same_key_as_bearer_token(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "tts-key")
    _authorize(request_with_headers({"Authorization": "Bearer tts-key"}))


def test_proxy_rejects_invalid_key(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "tts-key")
    try:
        _authorize(request_with_headers({"X-API-Key": "wrong"}))
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("invalid API key was accepted")
