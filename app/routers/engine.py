"""Full authenticated facade for the pure-C Qwen3-TTS server."""

from typing import Literal

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.services.c_tts import speakers, stream_synthesize, synthesize, to_c_markup

router = APIRouter(prefix="/api/v1/engine", tags=["c-tts-engine"])

EMOTIONS = (
    "neutral", "joy", "happy", "excited", "proud", "news", "dramatic", "calm",
    "sad", "gloomy", "annoyed", "stern", "anger", "fear", "disgust", "surprise",
    "contempt", "awe", "nostalgia", "disapproval", "remorse", "outrage", "despair",
)


class EngineSpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8192)
    speaker: str = "vivian"
    language: str = "German"
    emotion: str | None = None
    instruct: str | None = None
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, ge=0.0, le=3.0)
    temperature: float = Field(default=1.1, ge=0.0, le=2.0)
    top_k: int = Field(default=50, ge=0, le=3072)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    rep_penalty: float = Field(default=1.08, ge=0.5, le=2.0)
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    chunk_frames: int = Field(default=10, ge=2, le=250)
    pause_ms: int = Field(default=350, ge=0, le=5000)
    response_format: Literal["wav", "base64"] = "wav"


def _payload(req: EngineSpeechRequest) -> dict:
    return {
        "text": req.text, "speaker": req.speaker.lower(), "language": req.language,
        "emotion": req.emotion, "instruct": req.instruct, "rate": req.rate,
        "volume": req.volume, "temperature": req.temperature, "top_k": req.top_k,
        "top_p": req.top_p, "rep_penalty": req.rep_penalty, "seed": req.seed,
        "chunk_frames": req.chunk_frames, "pause_ms": req.pause_ms,
    }


@router.post("/speech")
async def speech(req: EngineSpeechRequest, _=Depends(verify_api_key)):
    import base64
    audio = await synthesize(
        req.text, speaker=req.speaker.lower(), language=req.language, rate=req.rate,
        pause_ms=req.pause_ms, emotion=req.emotion, instruct=req.instruct,
        volume=req.volume, temperature=req.temperature, top_k=req.top_k,
        top_p=req.top_p, rep_penalty=req.rep_penalty, seed=req.seed,
    )
    if req.response_format == "base64":
        return {"audio": base64.b64encode(audio).decode(), "sample_rate": 24000, "format": "wav"}
    return Response(audio, media_type="audio/wav")


@router.post("/speech/stream")
async def speech_stream(req: EngineSpeechRequest, _=Depends(verify_api_key)):
    return StreamingResponse(
        stream_synthesize({key: value for key, value in _payload(req).items() if value is not None}),
        media_type="audio/L16;rate=24000;channels=1",
        headers={"X-Audio-Format": "s16le", "X-Sample-Rate": "24000"},
    )


@router.get("/speakers")
async def engine_speakers(_=Depends(verify_api_key)):
    return {"speakers": await speakers()}


@router.get("/capabilities")
async def capabilities(_=Depends(verify_api_key)):
    return {
        "engine": "gabriele-mastrapasqua/qwen3-tts", "sample_rate": 24000,
        "languages": ["Chinese", "English", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian"],
        "emotions": EMOTIONS,
        "features": ["wav", "pcm-streaming", "inline-emotions", "pauses", "laugh", "sigh", "rate", "volume", "instruct", "seed", "sampling", "preset-voices", "cuda", "int8", "int4"],
        "markup_example": "[calm] Langsam. [pause:500ms] [fear] Was war das? [sigh]",
    }
