"""Persistent German story mode with narrator, characters, TTS and RAG memory."""

import asyncio
import json
import logging
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.auth import verify_api_key
from app.routers.debate import _create_speaker_voice_prompt, _tts_speech
from app.models.manager import get_voice_clone_prompt
from app.routers.archive import save_story_to_archive
from app.services.lm_studio import get_lm_studio_client
from app.services.session_store import (
    add_memory,
    list_sessions,
    load_session,
    retrieve_memories,
    save_session,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/story", tags=["story"])


class StoryCharacter(BaseModel):
    id: str = ""
    name: str
    role: str
    personality: str
    voice_description: str
    model_name: str = ""
    language: str = "German"
    voice_prompt_id: str = ""


class CreateStoryRequest(BaseModel):
    title: str = Field(..., min_length=1)
    premise: str = Field(..., min_length=1)
    genre: str = "Fantasy"
    model_name: str = ""
    characters: list[StoryCharacter] = Field(default_factory=list)
    max_scenes: int = Field(default=100, ge=1, le=1000)
    delay_between_turns: float = Field(default=0.5, ge=0, le=30)
    delivery_mode: str = Field(default="live", pattern="^(live|prerecorded)$")


class StoryMessage(BaseModel):
    speaker_id: str
    speaker_name: str
    text: str
    audio_base64: Optional[str] = None
    timestamp: str
    scene: int


def _default_characters() -> list[StoryCharacter]:
    narrator_voices = [
        "Warme deutsche Altstimme, ruhig, bildhaft und geheimnisvoll",
        "Klare deutsche Erzählerinnenstimme, lebendig und kinoreif",
        "Reife deutsche Frauenstimme, sanft, dunkel und spannungsvoll",
    ]
    heroine_voices = [
        "Junge klare deutsche Frauenstimme, mutig und emotional",
        "Helle deutsche Frauenstimme, neugierig, schnell und ausdrucksstark",
        "Ruhige deutsche Frauenstimme, entschlossen und nahbar",
    ]
    companion_voices = [
        "Ruhige deutsche Männerstimme mit trockenem Humor",
        "Warme tiefe deutsche Männerstimme, bedacht und geheimnisvoll",
        "Junge deutsche Männerstimme, wach, ironisch und loyal",
    ]
    profiles = random.sample([
        "Mutig, neugierig und empathisch; handelt eigenständig und macht glaubwürdige Fehler.",
        "Analytisch und vorsichtig, verbirgt aber eine impulsive Seite.",
        "Willensstark, humorvoll und loyal; stellt unbequeme Fragen.",
    ], 2)
    return [
        StoryCharacter(
            id="narrator", name="Erzählerin", role="Erzählerin",
            personality="Du erzählst atmosphärisch, präzise und spannend, ohne die Figuren zu bevormunden.",
            voice_description=random.choice(narrator_voices),
        ),
        StoryCharacter(
            id="mara", name="Mara", role="Protagonistin",
            personality=profiles[0],
            voice_description=random.choice(heroine_voices),
        ),
        StoryCharacter(
            id="elias", name="Elias", role="Begleiter",
            personality=profiles[1] + " Verfolgt außerdem ein verborgenes Ziel.",
            voice_description=random.choice(companion_voices),
        ),
    ]


_sessions: dict[str, dict] = {}
_queues: dict[str, asyncio.Queue] = {}


def _restore(session_id: str) -> dict | None:
    if session_id in _sessions:
        return _sessions[session_id]
    raw = load_session(session_id, "story")
    if not raw:
        return None
    raw["characters"] = [StoryCharacter(**character) for character in raw.get("characters", [])]
    raw["messages"] = [StoryMessage(**message) for message in raw.get("messages", [])]
    raw["running_task"] = None
    raw["status"] = "stopped" if raw.get("status") == "running" else raw.get("status", "stopped")
    _sessions[session_id] = raw
    _queues[session_id] = asyncio.Queue()
    return raw


@router.post("/create")
async def create_story(req: CreateStoryRequest, _=Depends(verify_api_key)):
    session_id = str(uuid.uuid4())[:8]
    characters = req.characters or _default_characters()
    for index, character in enumerate(characters):
        if not character.id:
            character.id = f"character_{index}"
    if not any(character.id == "narrator" or character.role.lower() == "erzählerin" for character in characters):
        characters.insert(0, _default_characters()[0])
    now = datetime.now(timezone.utc).isoformat()
    session = {
        "session_id": session_id,
        "title": req.title,
        "premise": req.premise,
        "genre": req.genre,
        "model_name": req.model_name,
        "characters": characters,
        "messages": [],
        "status": "idle",
        "current_scene": 0,
        "current_character_index": 0,
        "max_scenes": req.max_scenes,
        "delay_between_turns": req.delay_between_turns,
        "delivery_mode": req.delivery_mode,
        "created_at": now,
        "updated_at": now,
        "running_task": None,
    }
    _sessions[session_id] = session
    _queues[session_id] = asyncio.Queue()
    save_session("story", session)
    return _state(session)


@router.get("")
async def stories(_=Depends(verify_api_key)):
    return list_sessions("story")


@router.get("/{session_id}")
async def get_story(session_id: str, _=Depends(verify_api_key)):
    session = _restore(session_id)
    if not session:
        raise HTTPException(404, "Story not found")
    return _state(session)


@router.post("/{session_id}/start")
async def start_story(session_id: str, _=Depends(verify_api_key)):
    session = _restore(session_id)
    if not session:
        raise HTTPException(404, "Story not found")
    if session["status"] == "running":
        return {"status": "running", "session_id": session_id}
    if not await get_lm_studio_client().is_healthy():
        raise HTTPException(503, "LM Studio is not reachable")
    session["status"] = "running"
    queue = _queues.setdefault(session_id, asyncio.Queue())
    await queue.put({"event": "status", "data": {"status": "running"}})
    task = asyncio.create_task(_story_loop(session_id))
    session["running_task"] = task
    save_session("story", session)
    return {"status": "running", "session_id": session_id}


@router.post("/{session_id}/stop")
async def stop_story(session_id: str, _=Depends(verify_api_key)):
    session = _restore(session_id)
    if not session:
        raise HTTPException(404, "Story not found")
    session["status"] = "stopped"
    task = session.get("running_task")
    if task:
        task.cancel()
    save_session("story", session)
    save_story_to_archive(session_id, session)
    return {"status": "stopped"}


@router.get("/{session_id}/stream")
async def stream_story(session_id: str):
    if not _restore(session_id):
        raise HTTPException(404, "Story not found")
    queue = _queues.setdefault(session_id, asyncio.Queue())

    async def events():
        try:
            while True:
                message = await queue.get()
                if message is None:
                    break
                yield {**message, "data": json.dumps(message["data"], ensure_ascii=False)}
        except asyncio.CancelledError:
            pass

    return EventSourceResponse(events())


async def _story_loop(session_id: str):
    session = _sessions[session_id]
    queue = _queues[session_id]
    try:
        while session["status"] == "running" and session["current_scene"] < session["max_scenes"]:
            characters = session["characters"]
            index = session["current_character_index"] % len(characters)
            character = characters[index]
            scene = session["current_scene"] + 1
            await queue.put({"event": "turn", "data": {
                "speaker_id": character.id, "speaker_name": character.name, "scene": scene,
            }})
            total_turns = session["max_scenes"] * len(characters)
            completed_turns = session["current_scene"] * len(characters) + index
            await queue.put({"event": "progress", "data": {
                "percent": 10,
                "label": f"Text für {character.name} wird erzeugt",
            }})
            if not character.voice_prompt_id or get_voice_clone_prompt(character.voice_prompt_id) is None:
                await queue.put({"event": "progress", "data": {
                    "percent": 15,
                    "label": f"Feste Stimme für {character.name} wird einmalig vorbereitet",
                }})
                character.voice_prompt_id = await _create_speaker_voice_prompt(character)
                save_session("story", session)
            message = await _generate_turn(session, character, scene, queue)
            await queue.put({"event": "progress", "data": {
                "percent": 90,
                "label": f"Stimme für {character.name} ist fertig",
            }})
            session["messages"].append(StoryMessage(**message))
            add_memory(session_id, "story", character.name, message["text"])
            session["current_character_index"] = (index + 1) % len(characters)
            if session["current_character_index"] == 0:
                session["current_scene"] = scene
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_session("story", session)
            await queue.put({"event": "progress", "data": {
                "percent": 100,
                "label": "Story gespeichert",
            }})
            await queue.put({"event": "message", "data": message})
            if session["delay_between_turns"]:
                await asyncio.sleep(session["delay_between_turns"])
        if session["status"] == "running":
            session["status"] = "finished"
            await queue.put({"event": "status", "data": {"status": "finished"}})
    except asyncio.CancelledError:
        session["status"] = "stopped"
    except Exception as exc:
        logger.exception("Story loop failed")
        session["status"] = "stopped"
        await queue.put({"event": "error", "data": {"message": str(exc)}})
    finally:
        session["running_task"] = None
        save_session("story", session)
        save_story_to_archive(session_id, session)
        await queue.put(None)


async def _generate_turn(
    session: dict, character: StoryCharacter, scene: int,
    progress_queue: asyncio.Queue | None = None,
) -> dict:
    recent = session["messages"][-14:]
    query = " ".join(message.text for message in recent[-4:]) or session["premise"]
    memories = retrieve_memories(session["session_id"], query, limit=5)
    cast = "\n".join(
        f"- {member.name} ({member.role}): {member.personality}"
        for member in session["characters"]
    )
    memory_block = "\n".join(f"- {memory}" for memory in memories) or "- Noch keine Langzeiterinnerungen."
    is_narrator = character.id == "narrator" or character.role.lower() == "erzählerin"
    role_rules = (
        "Die Erzählerin beschreibt alles in der dritten Person. Sie ist niemals selbst Figur, "
        "sagt niemals 'ich' und spricht nicht stellvertretend für Mara oder Elias. Sie schildert "
        "sichtbare, konkrete Sims-artige Alltagshandlungen, Orte, Gegenstände, Bedürfnisse und Folgen."
        if is_narrator else
        f"{character.name} handelt nur als eigene Figur. Beschreibe eine konkrete Handlung und "
        "natürlichen Dialog; kontrolliere oder vertone keine andere Figur und nicht die Erzählerin."
    )
    continuity = "\n".join(f"{item.speaker_name}: {item.text}" for item in recent[-8:]) or "Noch kein vorheriger Beitrag."
    system = f"""Du bist Autor einer zusammenhängenden deutschen {session['genre']}-Fortsetzungsgeschichte.
Titel: {session['title']}
Prämisse: {session['premise']}
Figuren:
{cast}

Aktueller Beitrag: {character.name} ({character.role}).
Rollenprofil: {character.personality}
Rollenregeln: {role_rules}

RAG-Erinnerungen aus früheren Szenen:
{memory_block}

Unmittelbarer bisheriger Verlauf (nur als Kontext, niemals kopieren):
{continuity}

Regeln:
- Setze exakt bei der letzten Handlung an und verursache eine neue, logische Folge.
- Pro Beitrag muss sich die Situation verändern oder eine neue Information sichtbar werden.
- Wiederhole weder Handlung, Dialog noch Formulierungen aus dem bisherigen Verlauf.
- Verrate nicht den gesamten Plot und beende die Geschichte nicht vorzeitig.
- 2 bis 3 natürliche Sätze mit insgesamt 25 bis 60 Wörtern, vollständig auf Deutsch.
- Erzählerin beschreibt nur in dritter Person; Figuren handeln und sprechen aus ihrer eigenen Perspektive.
- Gib ausschließlich den neuen Erzähltext aus: keine Sprecherbezeichnung, keine Wortzahl, keine Erklärung.
- Nutze sparsam TTS-Emotions-Tags wie (calm), (tense), (whispering), (excited)."""
    turn_instruction = (
        f"/no_think\nSzene {scene}, Erzählerin. Beschreibe in dritter Person, was als Nächstes geschieht. "
        "Greife das letzte konkrete Detail auf, führe es aber zu einer neuen Entdeckung oder Konsequenz. Keine Dialogwiederholung."
        if is_narrator else
        f"/no_think\nSzene {scene}, Fokusfigur {character.name}. Nur {character.name} handelt oder spricht. "
        "Reagiere auf den unmittelbar letzten Beitrag und treibe die Handlung mit einer neuen Entscheidung voran."
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": turn_instruction}]
    text = ""
    recent_tokens = [set(re.findall(r"[a-zäöüß]{4,}", item.text.lower())) for item in recent[-6:]]
    for attempt in range(3):
        response = await get_lm_studio_client().chat_completion(
            messages, model=character.model_name or session["model_name"],
            temperature=0.68 + attempt * 0.08, max_tokens=150,
        )
        text = response["choices"][0]["message"]["content"].strip()
        text = re.sub(r"^(?:Erzählerin|Mara|Elias)\s*:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*\(\s*\d+\s+Wörter\s*\)\s*$", "", text, flags=re.IGNORECASE)
        tokens = set(re.findall(r"[a-zäöüß]{4,}", text.lower()))
        similarity = max((len(tokens & old) / max(1, len(tokens | old)) for old in recent_tokens), default=0)
        repeated_quote = any(
            quote.lower() in " ".join(item.text.lower() for item in recent[-6:])
            for quote in re.findall(r"[„\"]([^“\"]+)[“\"]", text)
        )
        if len(tokens) >= 8 and similarity < 0.58 and not repeated_quote:
            break
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": "/no_think\nZu ähnlich oder sprachlich fehlerhaft. Schreibe einen völlig neuen nächsten Handlungsschritt ohne bekannte Sätze oder Dialoge zu wiederholen."})
    if progress_queue is not None:
        preview = {
            "speaker_id": character.id, "speaker_name": character.name,
            "text": text, "audio_base64": None,
            "timestamp": datetime.now(timezone.utc).isoformat(), "scene": scene,
        }
        await progress_queue.put({"event": "text", "data": preview})
        await progress_queue.put({"event": "progress", "data": {
            "percent": 45,
            "label": f"Text für {character.name} fertig · Stimme wird erzeugt",
        }})
    audio = await _tts_speech(
        text, character.voice_description, character.language,
        voice_prompt_id=character.voice_prompt_id,
    )
    return {
        "speaker_id": character.id,
        "speaker_name": character.name,
        "text": text,
        "audio_base64": audio,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scene": scene,
    }


def _state(session: dict) -> dict:
    return {
        key: value for key, value in session.items()
        if key != "running_task"
    }
