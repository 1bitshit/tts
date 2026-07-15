"""
Archive API endpoints — saved debates, voice prompts, and audio clips.
"""
import io
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/archive", tags=["archive"])

ARCHIVE_DIR = Path("/notebooks/workspace/bkg/data/archive")
DEBATES_FILE = ARCHIVE_DIR / "debates.json"
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


# ── Endpoints ───────────────────────────────────────────────────────

@router.get("/debates")
async def list_archived_debates(_=Depends(verify_api_key)):
    debates = _read_json(DEBATES_FILE)
    # Return only metadata (no full messages) for list view
    return [
        {
            "session_id": d["session_id"],
            "topic": d["topic"],
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
        # Add prompts file
        if PROMPTS_FILE.exists():
            zf.write(PROMPTS_FILE, PROMPTS_FILE.name)
        # Add clips
        clips_dir = ARCHIVE_DIR / "clips"
        if clips_dir.exists():
            for f in clips_dir.iterdir():
                if f.suffix in (".wav", ".mp3"):
                    zf.write(f, f"clips/{f.name}")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=archive-{datetime.now(timezone.utc).strftime('%Y%m%d')}.zip"},
    )
