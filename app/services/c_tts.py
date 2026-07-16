"""Client and markup adapter for the pure-C Qwen3-TTS HTTP server."""

import hashlib
import re

import httpx

from app.config import settings


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
) -> bytes:
    payload = {
        "text": to_c_markup(text, pause_ms=pause_ms),
        "speaker": speaker,
        "language": "German" if language.lower() in {"auto", "german", "de", "deutsch"} else language,
        "rate": rate,
        "temperature": 1.1,
        "rep_penalty": 1.08,
        # Stable per passage, but not the same acoustic sampling trajectory for
        # every paragraph. This avoids a mechanical repeated cadence.
        "seed": int.from_bytes(
            hashlib.sha256(f"{speaker}\0{text}".encode("utf-8")).digest()[:4], "big"
        ),
    }
    async with httpx.AsyncClient(timeout=settings.c_tts_timeout_seconds) as client:
        response = await client.post(f"{settings.c_tts_url.rstrip('/')}/v1/tts", json=payload)
        response.raise_for_status()
        return response.content


async def is_healthy() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{settings.c_tts_url.rstrip('/')}/v1/health")
            return response.is_success
    except httpx.HTTPError:
        return False
