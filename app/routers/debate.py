"""
Debate API endpoints — multi-agent debate with LM Studio LLMs + Qwen TTS.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
import httpx

from app.auth import verify_api_key
from app.models.manager import model_manager, store_voice_clone_prompt, get_voice_clone_prompt
from app.routers.archive import save_debate_to_archive
from app.services.lm_studio import get_lm_studio_client
from app.services.session_store import add_memory, list_sessions, load_session, retrieve_memories, save_session
from app.utils.audio import numpy_to_wav_bytes, apply_speed
from app.utils.emotion_tags import generate_with_emotion_tags, has_emotion_tags, strip_emotion_tags
from app.utils.metrics import PerformanceTracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/debate", tags=["debate"])

# ── Schemas ────────────────────────────────────────────────────────

class SpeakerConfig(BaseModel):
    id: str = ""
    name: str = "Debater"
    personality: str = "You are a thoughtful debater who makes logical arguments."
    model_name: str = ""
    voice_description: str = "A clear, professional speaking voice"
    language: str = "Auto"
    voice_prompt_id: str = ""  # Created on debate start via Voice Design → Clone pipeline

class CreateDebateRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    speakers: List[SpeakerConfig] = Field(default_factory=lambda: _default_speakers())
    max_rounds: int = Field(default=10, ge=1, le=100)
    auto_advance: bool = True
    delay_between_speakers: float = 1.0
    delivery_mode: str = Field(default="live", pattern="^(live|prerecorded)$")

class AddSpeakerRequest(BaseModel):
    name: str
    personality: str = "You are a thoughtful debater who makes logical arguments."
    model_name: str = ""
    voice_description: str = "A clear, professional speaking voice"
    language: str = "Auto"

class TickRequest(BaseModel):
    speaker_id: Optional[str] = None

class DownloadModelRequest(BaseModel):
    model_id: str = Field(..., description="HuggingFace model ID to download via LM Studio")

class DebateMessage(BaseModel):
    speaker_id: str
    speaker_name: str
    text: str
    audio_base64: Optional[str] = None
    timestamp: str = ""
    round: int = 1

class DebateState(BaseModel):
    session_id: str
    topic: str
    speakers: List[SpeakerConfig]
    messages: List[DebateMessage]
    status: str  # idle | running | paused | stopped | finished
    current_round: int
    current_speaker_index: int
    max_rounds: int
    auto_advance: bool

# ── In-memory session store ─────────────────────────────────────────

_sessions: Dict[str, dict] = {}
_sse_queues: Dict[str, asyncio.Queue] = {}


def _restore_debate(session_id: str) -> Optional[dict]:
    if session_id in _sessions:
        return _sessions[session_id]
    raw = load_session(session_id, "debate")
    if not raw:
        return None
    raw["speakers"] = [SpeakerConfig(**speaker) for speaker in raw.get("speakers", [])]
    raw["messages"] = [DebateMessage(**message) for message in raw.get("messages", [])]
    raw["running_task"] = None
    raw["status"] = "stopped" if raw.get("status") == "running" else raw.get("status", "stopped")
    _sessions[session_id] = raw
    _sse_queues[session_id] = asyncio.Queue()
    return raw

def _default_speakers() -> List[SpeakerConfig]:
    return [
        SpeakerConfig(
            id="speaker_1", name="Klara",
            personality="Du bist Klara, eine leidenschaftliche progressive Debattantin. Du argumentierst für soziale Gerechtigkeit, Umweltschutz und evidenzbasierte Politik. Nutze logische Argumente und nenne Beispiele aus der Praxis.",
            voice_description="Eine warme, artikulierte Frauenstimme mit einem selbstbewussten, überzeugenden Ton",
            language="German",
        ),
        SpeakerConfig(
            id="speaker_2", name="Lukas",
            personality="Du bist Lukas, ein scharfsinniger konservativer Debattant. Du argumentierst für freie Märkte, individuelle Freiheit und traditionelle Werte. Nutze logische Argumente und nenne Beispiele aus der Praxis.",
            voice_description="Eine ruhige, autoritäre Männerstimme mit einem bedachten, gemessenen Ton",
            language="German",
        ),
        SpeakerConfig(
            id="speaker_3", name="Mia",
            personality="Du bist Mia, eine pragmatische liberale Debattantin. Du bewertest jedes Argument nach seinen Vorzügen und suchst nach ausgewogenen Lösungen.",
            voice_description="Eine helle, junge Frauenstimme mit einem klaren, präzisen Ton",
            language="German",
        ),
    ]

# ── Helpers ─────────────────────────────────────────────────────────

def _build_system_prompt(topic: str, speaker: SpeakerConfig, all_speakers: List[SpeakerConfig]) -> str:
    others = [s for s in all_speakers if s.id != speaker.id]
    others_desc = "\n".join(f"- {s.name}: {s.personality}" for s in others)
    return (
        f"Du nimmst an einer formellen Debatte teil.\n\n"
        f"Thema: {topic}\n\n"
        f"Deine Rolle:\n{speaker.personality}\n\n"
        f"Andere Teilnehmer:\n{others_desc}\n\n"
        f"Regeln:\n"
        f"- Formuliere klare, logische Argumente\n"
        f"- Gehe auf die Argumente der anderen ein\n"
        f"- Bleibe beim Thema\n"
        f"- Halte dich kurz (2-4 Absätze)\n"
        f"- Wiederhole dich nicht\n"
        f"- Verwende Emotions-Tags wie (calm), (angry), (thoughtful), (surprised), "
        f"(confident), (sarcastic), (laughing), (serious) vor Sätzen, "
        f"um die passende Emotion für dein Argument auszudrücken. Beispiel:\n"
        f'  (calm) Die Daten zeigen eindeutig, dass diese Politik funktioniert.\n'
        f'  (surprised) Doch mein Gegner scheint diese Fakten zu ignorieren!\n'
        f'  (confident) Daher ist meine Position die richtige.\n'
        f"- Wähle Emotionen, die natürlich zum Ton deines Arguments passen\n"
        f"- Antworte AUF DEUTSCH. Die gesamte Debatte wird auf Deutsch geführt."
    )

async def _tts_speech(
    text: str, voice_desc: str, language: str,
    voice_prompt_id: str = "",
) -> Optional[str]:
    try:
        if voice_prompt_id:
            prompt = get_voice_clone_prompt(voice_prompt_id)
            if not prompt:
                logger.warning(f"Voice prompt {voice_prompt_id} not found, falling back to VoiceDesign")
                return await _tts_speech_design(text, voice_desc, language)

            def _render_clone():
                base_model = model_manager.get_base_model()

                def _generate(instruct: str, segment_text: str, **kw) -> tuple:
                    return base_model.generate_voice_clone(
                        text=f"{instruct} {segment_text}".strip() if instruct else segment_text,
                        language=language,
                        voice_clone_prompt=prompt,
                    )

                return generate_with_emotion_tags(
                    text=text, generate_func=_generate, base_instruct=voice_desc, sr=24000,
                )

            audio, sr = await asyncio.to_thread(_render_clone)
        else:
            return await _tts_speech_design(text, voice_desc, language)

        import base64, io, soundfile as sf
        buf = io.BytesIO()
        sf.write(buf, audio, sr, format="wav")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning(f"TTS failed for debate message: {e}")
        return None


async def _tts_speech_design(text: str, voice_desc: str, language: str) -> Optional[str]:
    try:
        def _render_design():
            model = model_manager.get_voice_design_model()

            def _generate(instruct: str, segment_text: str, **kw) -> tuple:
                return model.generate_voice_design(
                    text=segment_text,
                    language=language,
                    instruct=instruct or voice_desc,
                )

            return generate_with_emotion_tags(
                text=text, generate_func=_generate, base_instruct=voice_desc, sr=24000,
            )

        audio, sr = await asyncio.to_thread(_render_design)
        import base64, io, soundfile as sf
        buf = io.BytesIO()
        sf.write(buf, audio, sr, format="wav")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning(f"TTS design fallback failed: {e}")
        return None


async def _create_speaker_voice_prompt(speaker: SpeakerConfig) -> str:
    try:
        def _prepare_voice():
            design_model = model_manager.get_voice_design_model()
            ref_text = "Guten Tag. Ich erzähle mit klarer Stimme und natürlicher Betonung."
            ref_wavs, sr = design_model.generate_voice_design(
                text=ref_text, language=speaker.language or "German",
                instruct=speaker.voice_description,
            )
            base_model = model_manager.get_base_model()
            return base_model.create_voice_clone_prompt(
                ref_audio=(ref_wavs[0], sr), ref_text=ref_text, x_vector_only_mode=True,
            )

        prompt = await asyncio.to_thread(_prepare_voice)
        import uuid as _uuid
        prompt_id = str(_uuid.uuid4())[:8]
        store_voice_clone_prompt(prompt_id, prompt)
        return prompt_id
    except Exception as e:
        logger.warning(f"Failed to create voice prompt for {speaker.name}: {e}")
        return ""

# ── Endpoints ───────────────────────────────────────────────────────

@router.post("/create")
async def create_debate(req: CreateDebateRequest, _=Depends(verify_api_key)):
    session_id = str(uuid.uuid4())[:8]
    for i, s in enumerate(req.speakers):
        if not s.id:
            s.id = f"speaker_{i}"
    now = datetime.now(timezone.utc).isoformat()
    session = {
        "session_id": session_id,
        "topic": req.topic,
        "speakers": req.speakers,
        "messages": [],
        "status": "idle",
        "current_round": 0,
        "current_speaker_index": 0,
        "max_rounds": req.max_rounds,
        "auto_advance": req.auto_advance,
        "delay_between_speakers": req.delay_between_speakers,
        "delivery_mode": req.delivery_mode,
        "created_at": now,
        "updated_at": now,
        "running_task": None,
    }
    _sessions[session_id] = session
    _sse_queues[session_id] = asyncio.Queue()
    save_session("debate", session)
    return {
        "session_id": session_id,
        "topic": req.topic,
        "speakers": [s.model_dump() for s in req.speakers],
        "status": "idle",
    }

@router.post("/{session_id}/start")
async def start_debate(session_id: str, _=Depends(verify_api_key)):
    session = _restore_debate(session_id)
    if not session:
        raise HTTPException(404, "Debate session not found")
    if session["status"] == "running":
        raise HTTPException(400, "Debate already running")

    session["status"] = "running"
    session["updated_at"] = datetime.now(timezone.utc).isoformat()

    lm = get_lm_studio_client()
    healthy = await lm.is_healthy()
    q = _sse_queues[session_id]
    await q.put({"event": "status", "data": {"status": "running", "lm_studio_connected": healthy}})

    if not healthy:
        await q.put({"event": "error", "data": {"message": "LM Studio not reachable on port 1234"}})
        session["status"] = "stopped"
        return {"status": "error", "message": "LM Studio not reachable"}

    # Create consistent voice prompts for each speaker (Voice Design → Clone pipeline)
    await q.put({"event": "status", "data": {"status": "creating_voices"}})
    for voice_index, speaker in enumerate(session["speakers"]):
        await q.put({"event": "progress", "data": {
            "percent": round(voice_index / len(session["speakers"]) * 5),
            "label": f"Stimme für {speaker.name} wird vorbereitet",
        }})
        if not speaker.voice_prompt_id:
            speaker.voice_prompt_id = await _create_speaker_voice_prompt(speaker)
            if speaker.voice_prompt_id:
                logger.info(f"Created voice prompt {speaker.voice_prompt_id} for {speaker.name}")
            await q.put({
                "event": "voice_ready",
                "data": {"speaker_id": speaker.id, "speaker_name": speaker.name}
            })

    asyncio.create_task(_run_debate_loop(session_id))
    save_session("debate", session)
    return {"status": "running", "session_id": session_id}

@router.post("/{session_id}/stop")
async def stop_debate(session_id: str, _=Depends(verify_api_key)):
    session = _restore_debate(session_id)
    if not session:
        raise HTTPException(404, "Debate session not found")
    session["status"] = "stopped"
    if session.get("running_task"):
        session["running_task"].cancel()
    q = _sse_queues.get(session_id)
    if q:
        await q.put({"event": "status", "data": {"status": "stopped"}})
        await q.put(None)
    save_session("debate", session)
    return {"status": "stopped"}


@router.get("/sessions")
async def saved_debate_sessions(_=Depends(verify_api_key)):
    return list_sessions("debate")

@router.get("/{session_id}")
async def get_debate(session_id: str):
    session = _restore_debate(session_id)
    if not session:
        raise HTTPException(404, "Debate session not found")
    return _session_to_state(session)

@router.get("/{session_id}/stream")
async def stream_debate(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Debate session not found")

    q = _sse_queues.get(session_id)
    if q is None:
        q = asyncio.Queue()
        _sse_queues[session_id] = q

    async def event_generator():
        try:
            while True:
                msg = await q.get()
                if msg is None:
                    break
                yield {**msg, "data": json.dumps(msg["data"], ensure_ascii=False)}
        except asyncio.CancelledError:
            pass

    return EventSourceResponse(event_generator())

# ── LM Studio management endpoints ──────────────────────────────────

@router.get("/lm/models")
async def list_lm_models(_=Depends(verify_api_key)):
    lm = get_lm_studio_client()
    try:
        models = await lm.list_models()
        return {"models": models, "connected": True}
    except Exception as e:
        return {"models": [], "connected": False, "error": str(e)}

@router.post("/lm/download")
async def download_lm_model(req: DownloadModelRequest, _=Depends(verify_api_key)):
    lm = get_lm_studio_client()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{lm.base_url.replace('/v1', '')}/api/v1/models/download",
                json={"model": req.model_id},
            )
            if resp.status_code == 200:
                return {"status": "started", "model_id": req.model_id}
            else:
                raise HTTPException(400, f"LM Studio download failed: {resp.text}")
    except httpx.ConnectError:
        raise HTTPException(503, "LM Studio not reachable")

@router.post("/{session_id}/speaker")
async def add_speaker(session_id: str, req: AddSpeakerRequest, _=Depends(verify_api_key)):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Debate session not found")
    if len(session["speakers"]) >= 6:
        raise HTTPException(400, "Maximum 6 speakers allowed")

    speaker = SpeakerConfig(
        id=f"speaker_{len(session['speakers'])}",
        name=req.name,
        personality=req.personality,
        model_name=req.model_name,
        voice_description=req.voice_description,
        language=req.language,
    )
    session["speakers"].append(speaker)
    return {"speakers": [s.model_dump() for s in session["speakers"]]}

@router.delete("/{session_id}/speaker/{speaker_id}")
async def remove_speaker(session_id: str, speaker_id: str, _=Depends(verify_api_key)):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Debate session not found")
    if len(session["speakers"]) <= 2:
        raise HTTPException(400, "Minimum 2 speakers required")

    session["speakers"] = [s for s in session["speakers"] if s.id != speaker_id]
    return {"speakers": [s.model_dump() for s in session["speakers"]]}

@router.put("/{session_id}/speaker/{speaker_id}")
async def update_speaker(
    session_id: str, speaker_id: str,
    req: AddSpeakerRequest, _=Depends(verify_api_key)
):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Debate session not found")
    for s in session["speakers"]:
        if s.id == speaker_id:
            s.name = req.name
            s.personality = req.personality
            s.model_name = req.model_name
            s.voice_description = req.voice_description
            s.language = req.language
            break
    else:
        raise HTTPException(404, "Speaker not found")
    return {"speakers": [s.model_dump() for s in session["speakers"]]}

@router.post("/{session_id}/tick")
async def debate_tick(session_id: str, req: TickRequest = TickRequest(), _=Depends(verify_api_key)):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Debate session not found")
    if session["status"] != "running":
        raise HTTPException(400, "Debate not running")

    speaker = None
    if req.speaker_id:
        speaker = next((s for s in session["speakers"] if s.id == req.speaker_id), None)
        if not speaker:
            raise HTTPException(404, "Speaker not found")
    else:
        speaker = session["speakers"][session["current_speaker_index"]]

    result = await _generate_speaker_response(session_id, speaker)
    session["messages"].append(DebateMessage(**result))
    session["current_speaker_index"] = (session["current_speaker_index"] + 1) % len(session["speakers"])
    if session["current_speaker_index"] == 0:
        session["current_round"] += 1
    return result

# ── Core debate loop ────────────────────────────────────────────────

async def _run_debate_loop(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        return
    q = _sse_queues.get(session_id)

    session["running_task"] = asyncio.current_task()

    try:
        while session["status"] == "running":
            round_num = session["current_round"] + 1
            if round_num > session["max_rounds"]:
                session["status"] = "finished"
                if q:
                    await q.put({"event": "status", "data": {"status": "finished"}})
                break

            speakers = session["speakers"]
            for idx, speaker in enumerate(speakers):
                if session["status"] != "running":
                    break

                session["current_speaker_index"] = idx
                session["current_round"] = round_num

                if q:
                    await q.put({
                        "event": "turn",
                        "data": {
                            "speaker_id": speaker.id,
                            "speaker_name": speaker.name,
                            "round": round_num,
                            "status": "thinking",
                        }
                    })

                try:
                    total_turns = session["max_rounds"] * len(speakers)
                    completed_turns = (round_num - 1) * len(speakers) + idx
                    if q:
                        await q.put({"event": "progress", "data": {
                            "percent": round(5 + completed_turns / total_turns * 95),
                            "label": f"Argument von {speaker.name} wird erzeugt",
                        }})
                    msg = await _generate_speaker_response(session_id, speaker)
                    session["messages"].append(DebateMessage(**msg))
                    add_memory(session_id, "debate", speaker.name, msg["text"])
                    session["updated_at"] = datetime.now(timezone.utc).isoformat()
                    save_session("debate", session)

                    if q:
                        await q.put({"event": "progress", "data": {
                            "percent": round(5 + (completed_turns + 1) / total_turns * 95),
                            "label": f"Runde {round_num}: Text und Stimme gespeichert",
                        }})
                        await q.put({"event": "message", "data": msg})

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Speaker {speaker.name} error: {e}")
                    session["status"] = "stopped"
                    if q:
                        await q.put({
                            "event": "error",
                            "data": {"speaker_id": speaker.id, "message": str(e)}
                        })
                    break

                if session["auto_advance"] and session["delay_between_speakers"] > 0:
                    await asyncio.sleep(session["delay_between_speakers"])

            session["current_round"] = round_num

        if session["status"] == "running":
            session["status"] = "finished"
            if q:
                await q.put({"event": "status", "data": {"status": "finished"}})

    except asyncio.CancelledError:
        logger.info(f"Debate {session_id} cancelled")
        session["status"] = "stopped"
    except Exception as e:
        logger.error(f"Debate loop error: {e}")
        session["status"] = "stopped"
        if q:
            await q.put({"event": "error", "data": {"message": str(e)}})
    finally:
        # Save to archive when debate ends
        try:
            save_debate_to_archive(session_id, session)
            save_session("debate", session)
        except Exception as e:
            logger.warning(f"Failed to archive debate {session_id}: {e}")
        if q:
            await q.put(None)
        session["running_task"] = None

async def _generate_speaker_response(session_id: str, speaker: SpeakerConfig) -> dict:
    session = _sessions[session_id]
    lm = get_lm_studio_client()

    system_prompt = _build_system_prompt(
        session["topic"], speaker, session["speakers"]
    )

    recent_query = " ".join(message.text for message in session["messages"][-4:]) or session["topic"]
    memories = retrieve_memories(session_id, recent_query, limit=5)
    if memories:
        system_prompt += (
            "\n\nRAG-Langzeitgedächtnis – diese früheren Argumente berücksichtigen, "
            "aber weder Inhalt noch Formulierung wiederholen:\n- " + "\n- ".join(memories)
        )

    messages = [{"role": "system", "content": system_prompt}]
    for m in session["messages"][-10:]:
        role = "assistant" if m.speaker_id == speaker.id else "user"
        messages.append({
            "role": role,
            "content": f"{m.speaker_name} says: {m.text}"
        })

    if not any(m["role"] == "user" for m in messages[-3:]):
        messages.append({
            "role": "user",
            "content": f"{speaker.name}, du bist dran. Präsentiere dein Argument."
        })

    response = await lm.chat_completion(
        messages=messages,
        model=speaker.model_name,
        temperature=0.8,
        max_tokens=512,
    )

    text = response["choices"][0]["message"]["content"].strip()

    audio_b64 = await _tts_speech(text, speaker.voice_description, speaker.language, voice_prompt_id=speaker.voice_prompt_id)

    return {
        "speaker_id": speaker.id,
        "speaker_name": speaker.name,
        "text": text,
        "audio_base64": audio_b64,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "round": session["current_round"],
    }

def _session_to_state(session: dict) -> DebateState:
    return DebateState(
        session_id=session["session_id"],
        topic=session["topic"],
        speakers=session["speakers"],
        messages=session["messages"],
        status=session["status"],
        current_round=session["current_round"],
        current_speaker_index=session["current_speaker_index"],
        max_rounds=session["max_rounds"],
        auto_advance=session["auto_advance"],
    )
