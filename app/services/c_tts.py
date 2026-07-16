"""Client and markup adapter for the pure-C Qwen3-TTS HTTP server."""

import asyncio
import hashlib
import logging
import re
from pathlib import Path

import httpx
from typing import AsyncIterator, Any

from app.config import settings

logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]
_RESTART_LOCK = asyncio.Lock()


FEMALE_VOICES = ("vivian", "serena", "sohee", "ono_anna")
MALE_VOICES = ("ryan", "aiden", "eric", "dylan", "uncle_fu")

_MOODS = {
    "happy": "joy", "joyful": "joy", "playful": "joy", "laughing": "joy",
    "excited": "excited", "surprised": "surprise", "fearful": "fear",
    "tense": "dramatic", "serious": "stern", "angry": "anger",
    "mock_angry": "annoyed", "sad": "sad", "crying": "sad",
    "relieved": "calm", "calm": "calm", "warm": "calm",
    "soft": "calm", "thoughtful": "calm", "whispering": "calm",
    "confident": "proud", "cold": "stern", "formal": "news",
}


def stable_preset(identity: str, female: bool | None = None) -> str:
    """Return the same built-in voice for the same role on every process."""
    lowered = identity.lower()
    if female is None:
        female = any(word in lowered for word in ("frau", "female", "weib", "hell", "serena", "vivian"))
    pool = FEMALE_VOICES if female else MALE_VOICES
    index = int.from_bytes(hashlib.sha256(identity.encode("utf-8")).digest()[:4], "big") % len(pool)
    return pool[index]


async def _run_script(path: str, action: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "bash", str(_ROOT / path), action,
        cwd=_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"{path} {action} failed: "
            f"{stderr.decode(errors='replace')[-1200:] or stdout.decode(errors='replace')[-1200:]}"
        )


async def _recover_gpu_tts() -> None:
    async with _RESTART_LOCK:
        logger.warning("Recovering GPU TTS runtime after upstream failure")
        await _run_script("setup/story-models.sh", "unload")
        await _run_script("setup/tts-engine.sh", "restart")
        await _run_script("setup/rust-engine.sh", "stop")
        await _run_script("setup/rust-engine.sh", "start")


def to_c_markup(text: str, pause_ms: int = 350) -> str:
    """Translate the app's legacy parenthesized tags into native C-engine markup."""
    def replace(match: re.Match[str]) -> str:
        tag = match.group(1).lower()
        if tag in {"pause", "long_pause"}:
            duration = pause_ms * (2 if tag == "long_pause" else 1)
            return f"[pause:{duration}ms]"
        if tag == "sigh":
            return "[sigh]"
        if tag == "breath":
            return "[pause:250ms]"
        return f"[{_MOODS.get(tag, 'neutral')}]"

    return re.sub(r"\(([a-z_]+)(?::[0-9.]+)?\)", replace, text, flags=re.IGNORECASE)


async def synthesize(
    text: str,
    *,
    speaker: str,
    language: str = "German",
    rate: float = 1.0,
    pause_ms: int = 350,
    emotion: str | None = None,
    instruct: str | None = None,
    volume: float = 1.0,
    temperature: float = 1.1,
    top_k: int = 50,
    top_p: float = 1.0,
    rep_penalty: float = 1.08,
    seed: int | None = None,
) -> bytes:
    payload = {
        "text": to_c_markup(text, pause_ms=pause_ms),
        "speaker": speaker,
        "language": "German" if language.lower() in {"auto", "german", "de", "deutsch"} else language,
        "rate": rate,
        "temperature": temperature,
        "top_k": top_k,
        "top_p": top_p,
        "rep_penalty": rep_penalty,
        "volume": volume,
        # Stable per passage, but not the same acoustic sampling trajectory for
        # every paragraph. This avoids a mechanical repeated cadence.
        "seed": seed if seed is not None else int.from_bytes(
            hashlib.sha256(f"{speaker}\0{text}".encode("utf-8")).digest()[:4], "big"
        ),
    }
    if emotion:
        payload["emotion"] = emotion
    if instruct:
        payload["instruct"] = instruct
    url = f"{settings.c_tts_url.rstrip('/')}/v1/tts"
    async with httpx.AsyncClient(timeout=settings.c_tts_timeout_seconds) as client:
        response = await client.post(url, json=payload)
        if response.status_code == 502:
            logger.error("Rust TTS returned 502: %s", response.text[:1200])
            await _recover_gpu_tts()
            response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.content


async def stream_synthesize(payload: dict[str, Any]) -> AsyncIterator[bytes]:
    """Proxy the engine's real chunked 24-kHz signed-16 PCM stream."""
    payload = dict(payload)
    payload["text"] = to_c_markup(payload["text"], pause_ms=int(payload.pop("pause_ms", 350)))
    if payload.get("language", "").lower() in {"auto", "german", "de", "deutsch"}:
        payload["language"] = "German"
    async with httpx.AsyncClient(timeout=settings.c_tts_timeout_seconds) as client:
        async with client.stream("POST", f"{settings.c_tts_url.rstrip('/')}/v1/tts/stream", json=payload) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk


async def speakers() -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{settings.c_tts_url.rstrip('/')}/v1/speakers")
        response.raise_for_status()
        return response.json().get("speakers", [])


async def is_healthy() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{settings.c_tts_url.rstrip('/')}/v1/health")
            return response.is_success
    except httpx.HTTPError:
        return False
