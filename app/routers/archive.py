"""
Archive API endpoints — saved debates, voice prompts, and audio clips.
"""
import io
import base64
import json
import logging
import os
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, FileResponse

from app.auth import verify_api_key
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/archive", tags=["archive"])

_data_dir = Path(settings.data_dir).expanduser()
if not _data_dir.is_absolute():
    _data_dir = Path(__file__).resolve().parents[2] / _data_dir
ARCHIVE_DIR = _data_dir / "archive"
DEBATES_FILE = ARCHIVE_DIR / "debates.json"
STORIES_FILE = ARCHIVE_DIR / "stories.json"
PROMPTS_FILE = ARCHIVE_DIR / "prompts.json"


def _ensure_archive_dir():
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _write_json(path: Path, data: list):
    _ensure_archive_dir()
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Save debate after completion ────────────────────────────────────

def save_debate_to_archive(session_id: str, debate_data: dict):
    debates = _read_json(DEBATES_FILE)
    # Remove existing entry with same session_id
    debates = [d for d in debates if d.get("session_id") != session_id]
    entry = {
        "session_id": session_id,
        "topic": debate_data.get("topic", ""),
        "category": debate_data.get("category", "Allgemein"),
        "teaser": debate_data.get("teaser", ""),
        "speakers": [
            {"id": s.id, "name": s.name, "personality": s.personality[:100], "voice_description": s.voice_description}
            for s in debate_data.get("speakers", [])
        ],
        "messages": [
            {"speaker_id": m.speaker_id, "speaker_name": m.speaker_name, "text": m.text[:500], "round": m.round, "timestamp": m.timestamp}
            for m in debate_data.get("messages", [])
        ],
        "status": debate_data.get("status", "finished"),
        "current_round": debate_data.get("current_round", 0),
        "max_rounds": debate_data.get("max_rounds", 10),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    debates.append(entry)
    _write_json(DEBATES_FILE, debates)
    logger.info(f"Debate {session_id} saved to archive")


def save_story_to_archive(session_id: str, story_data: dict):
    """Persist a resumable story manifest and its generated WAV clips."""
    stories = [s for s in _read_json(STORIES_FILE) if s.get("session_id") != session_id]
    clips_dir = ARCHIVE_DIR / "clips" / session_id
    clips_dir.mkdir(parents=True, exist_ok=True)
    messages = []
    for index, message in enumerate(story_data.get("messages", [])):
        value = message.model_dump() if hasattr(message, "model_dump") else dict(message)
        audio = value.pop("audio_base64", None)
        if audio:
            clip_name = f"{index:05d}-{value.get('speaker_id', 'voice')}.wav"
            try:
                (clips_dir / clip_name).write_bytes(base64.b64decode(audio))
                value["audio_file"] = f"clips/{session_id}/{clip_name}"
            except (ValueError, OSError) as exc:
                logger.warning("Could not archive story audio %s: %s", clip_name, exc)
        messages.append(value)
    characters = [
        value.model_dump() if hasattr(value, "model_dump") else dict(value)
        for value in story_data.get("characters", [])
    ]
    stories.append({
        key: story_data.get(key)
        for key in ("session_id", "title", "premise", "genre", "model_name", "status",
                    "current_scene", "max_scenes", "created_at", "updated_at")
    } | {"characters": characters, "messages": messages,
         "saved_at": datetime.now(timezone.utc).isoformat()})
    _write_json(STORIES_FILE, stories)
    logger.info("Story %s saved to archive", session_id)


# ── Endpoints ───────────────────────────────────────────────────────

@router.get("/debates")
async def list_archived_debates(_=Depends(verify_api_key)):
    debates = _read_json(DEBATES_FILE)
    # Return only metadata (no full messages) for list view
    return [
        {
            "session_id": d["session_id"],
            "topic": d["topic"],
            "category": d.get("category", "Allgemein"),
            "teaser": d.get("teaser", ""),
            "speakers": d["speakers"],
            "status": d["status"],
            "current_round": d["current_round"],
            "max_rounds": d["max_rounds"],
            "saved_at": d["saved_at"],
            "message_count": len(d.get("messages", [])),
        }
        for d in reversed(debates)
    ]


@router.get("/debates/{session_id}")
async def get_archived_debate(session_id: str):
    debates = _read_json(DEBATES_FILE)
    for d in debates:
        if d["session_id"] == session_id:
            return d
    raise HTTPException(404, "Debate not found in archive")


@router.get("/stories")
async def list_archived_stories(_=Depends(verify_api_key)):
    return list(reversed(_read_json(STORIES_FILE)))


@router.get("/stories/{session_id}")
async def get_archived_story(session_id: str, _=Depends(verify_api_key)):
    for story in _read_json(STORIES_FILE):
        if story.get("session_id") == session_id:
            return story
    raise HTTPException(404, "Story not found in archive")


@router.delete("/debates/{session_id}")
async def delete_archived_debate(session_id: str, _=Depends(verify_api_key)):
    debates = _read_json(DEBATES_FILE)
    debates = [d for d in debates if d["session_id"] != session_id]
    _write_json(DEBATES_FILE, debates)
    return {"status": "deleted"}


@router.get("/prompts")
async def list_archived_prompts(_=Depends(verify_api_key)):
    prompts = _read_json(PROMPTS_FILE)
    return list(reversed(prompts))


@router.post("/prompts")
async def save_prompt(prompt: dict, _=Depends(verify_api_key)):
    prompts = _read_json(PROMPTS_FILE)
    prompts.append({
        **prompt,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    })
    _write_json(PROMPTS_FILE, prompts)
    return {"status": "saved"}


@router.delete("/prompts/{prompt_id}")
async def delete_archived_prompt(prompt_id: str, _=Depends(verify_api_key)):
    prompts = _read_json(PROMPTS_FILE)
    prompts = [p for p in prompts if p.get("id") != prompt_id]
    _write_json(PROMPTS_FILE, prompts)
    return {"status": "deleted"}


@router.get("/clips")
async def list_archived_clips(_=Depends(verify_api_key)):
    clips_dir = ARCHIVE_DIR / "clips"
    if not clips_dir.exists():
        return []
    clips = []
    for f in sorted(clips_dir.iterdir(), key=os.path.getmtime, reverse=True):
        if f.suffix in (".wav", ".mp3"):
            clips.append({
                "id": f.stem,
                "name": f.name,
                "path": str(f.relative_to(ARCHIVE_DIR)),
                "size": f.stat().st_size,
                "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
    return clips


@router.get("/clips/{clip_id}/audio")
async def get_clip_audio(clip_id: str):
    clips_dir = ARCHIVE_DIR / "clips"
    if not clips_dir.exists():
        raise HTTPException(404, "No clips directory")
    for f in clips_dir.iterdir():
        if f.stem == clip_id and f.suffix in (".wav", ".mp3"):
            return FileResponse(str(f), media_type=f"audio/{f.suffix[1:]}")
    raise HTTPException(404, "Clip not found")


@router.get("/download-zip")
async def download_archive_zip(_=Depends(verify_api_key)):
    _ensure_archive_dir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add debates file
        if DEBATES_FILE.exists():
            zf.write(DEBATES_FILE, DEBATES_FILE.name)
        if STORIES_FILE.exists():
            zf.write(STORIES_FILE, STORIES_FILE.name)
        # Add prompts file
        if PROMPTS_FILE.exists():
            zf.write(PROMPTS_FILE, PROMPTS_FILE.name)
        # Add clips
        clips_dir = ARCHIVE_DIR / "clips"
        if clips_dir.exists():
            for f in clips_dir.rglob("*"):
                if f.is_file() and f.suffix in (".wav", ".mp3"):
                    zf.write(f, str(f.relative_to(ARCHIVE_DIR)))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=archive-{datetime.now(timezone.utc).strftime('%Y%m%d')}.zip"},
    )
