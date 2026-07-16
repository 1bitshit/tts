"""Persistent German story mode with narrator, characters, TTS and RAG memory."""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.auth import verify_api_key
from app.config import settings
from app.routers.debate import _tts_speech
from app.models.manager import get_voice_clone_prompt, model_manager, store_voice_clone_prompt
from app.routers.archive import save_story_to_archive
from app.services.lm_studio import get_lm_studio_client
from app.services.c_tts import is_healthy as c_tts_is_healthy, stable_preset
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
    voice_reference_audio: str = ""
    engine_voice: str = ""


class CreateStoryRequest(BaseModel):
    title: str = Field(..., min_length=1)
    premise: str = Field(..., min_length=1)
    genre: str = "Fantasy"
    model_name: str = ""
    characters: list[StoryCharacter] = Field(default_factory=list)
    narrator_gender: str = Field(default="female", pattern="^(female|male)$")
    character_gender: str = Field(default="mixed", pattern="^(female|male|mixed)$")
    character_count: int = Field(default=2, ge=1, le=6)
    band_minutes: int = Field(default=23, ge=5, le=120)
    max_scenes: int = Field(default=100, ge=1, le=1000)
    delay_between_turns: float = Field(default=0.5, ge=0, le=30)
    delivery_mode: str = Field(default="live", pattern="^(live|prerecorded)$")


class GenerateStoryIdeaRequest(BaseModel):
    genre: str = Field(..., min_length=1, max_length=80)
    character_count: int = Field(default=2, ge=1, le=6)
    character_gender: str = Field(default="mixed", pattern="^(female|male|mixed)$")
    narrator_gender: str = Field(default="female", pattern="^(female|male)$")
    model_name: str = "Qwen/Qwen3-4B-Instruct-2507-GGUF"


class StoryMessage(BaseModel):
    speaker_id: str
    speaker_name: str
    text: str
    audio_base64: Optional[str] = None
    timestamp: str
    scene: int


def _default_characters() -> list[StoryCharacter]:
    return _generated_characters("female", "mixed", 2)


def _generated_characters(narrator_gender: str, character_gender: str, count: int) -> list[StoryCharacter]:
    female_names = ["Mara", "Nora", "Lea", "Amira", "Clara", "Sofia"]
    male_names = ["Elias", "Noah", "Jonas", "Karim", "David", "Leon"]
    personalities = [
        "Mutig, neugierig und empathisch; handelt eigenständig und macht glaubwürdige Fehler.",
        "Analytisch und vorsichtig, verbirgt aber eine impulsive Seite.",
        "Willensstark, humorvoll und loyal; stellt unbequeme Fragen.",
        "Beobachtet genau, vertraut langsam und schützt die Gruppe in Gefahr.",
        "Kreativ und spontan; erkennt ungewöhnliche Lösungen, unterschätzt aber Risiken.",
        "Ruhig und pragmatisch; trägt ein Geheimnis mit sich, das die Handlung verändert.",
    ]
    narrator = StoryCharacter(
        id="narrator",
        name="Erzählerin" if narrator_gender == "female" else "Erzähler",
        role="Erzählerin" if narrator_gender == "female" else "Erzähler",
        personality="Erzählt atmosphärisch, präzise und spannend, ohne selbst zur Figur zu werden.",
        voice_description=(
            "Warme deutsche Frauenstimme, ruhig, bildhaft und kinoreif"
            if narrator_gender == "female" else
            "Warme deutsche Männerstimme, ruhig, bildhaft und kinoreif"
        ),
        engine_voice="vivian" if narrator_gender == "female" else "ryan",
    )
    characters = [narrator]
    for index in range(count):
        gender = character_gender
        if gender == "mixed":
            gender = "female" if index % 2 == 0 else "male"
        name = (female_names if gender == "female" else male_names)[index % 6]
        characters.append(StoryCharacter(
            id=f"character_{index + 1}",
            name=name,
            role="Hauptfigur" if index == 0 else "Nebenfigur",
            personality=personalities[index % len(personalities)],
            voice_description=(
                "Natürliche deutsche Frauenstimme, klar, eigenständig und emotional"
                if gender == "female" else
                "Natürliche deutsche Männerstimme, klar, eigenständig und emotional"
            ),
            engine_voice=(
                ("serena", "sohee", "ono_anna")[index % 3]
                if gender == "female" else
                ("aiden", "eric", "dylan", "uncle_fu")[index % 4]
            ),
        ))
    return characters


_sessions: dict[str, dict] = {}
_queues: dict[str, asyncio.Queue] = {}


def _volume_label(volume: int) -> str:
    return f"1.{max(0, volume - 1)}"


def _normalize_characters(characters: list[StoryCharacter]) -> list[StoryCharacter]:
    """Keep exactly one narrator and preserve every other story character."""
    normalized: list[StoryCharacter] = []
    narrator_seen = False
    for index, character in enumerate(characters):
        is_narrator = character.id == "narrator" or character.role.lower() in {"erzählerin", "erzähler"}
        if is_narrator:
            if narrator_seen:
                continue
            narrator_seen = True
            character.id = "narrator"
            if character.role.lower() not in {"erzählerin", "erzähler"}:
                character.role = "Erzählerin"
        elif not character.id:
            character.id = f"character_{index}"
        normalized.append(character)
    if not narrator_seen:
        normalized.insert(0, _default_characters()[0])
    return normalized


def _restore(session_id: str) -> dict | None:
    if session_id in _sessions:
        return _sessions[session_id]
    raw = load_session(session_id, "story")
    if not raw:
        return None
    raw["characters"] = _normalize_characters([
        StoryCharacter(**character) for character in raw.get("characters", [])
    ])
    raw["messages"] = [StoryMessage(**message) for message in raw.get("messages", [])]
    raw.setdefault("volume", 1)
    raw.setdefault("band_minutes", 23)
    raw.setdefault("scenes_per_volume", max(3, round(
        raw["band_minutes"] * 140 / (130 + 35 * max(1, len(raw["characters"]) - 1))
    )))
    if "volume_script_ready" not in raw:
        raw["volume_script_ready"] = bool(raw["messages"])
    if "narration_index" not in raw:
        raw["narration_index"] = next(
            (index for index, message in enumerate(raw["messages"]) if not message.audio_base64),
            len(raw["messages"]),
        )
    raw.setdefault("volume_message_start", 0)
    raw["running_task"] = None
    raw["status"] = "stopped" if raw.get("status") == "running" else raw.get("status", "stopped")
    _sessions[session_id] = raw
    _queues[session_id] = asyncio.Queue()
    return raw


@router.post("/idea")
async def generate_story_idea(req: GenerateStoryIdeaRequest, _=Depends(verify_api_key)):
    """Generate an editable German title and premise from the user's fixed selections."""
    gender_label = {
        "female": "weiblich",
        "male": "männlich",
        "mixed": "gemischt",
    }[req.character_gender]
    response = await get_lm_studio_client().chat_completion(
        [{
            "role": "system",
            "content": (
                "Du entwickelst originelle deutschsprachige Hörspielserien. Antworte ausschließlich "
                "als gültiges JSON-Objekt mit den Schlüsseln title und premise. Der Titel ist kurz und "
                "prägnant. Die Prämisse umfasst 2 bis 4 konkrete Sätze, nennt Ausgangslage, Hauptkonflikt "
                "und ein langfristiges Geheimnis. Keine Markdown-Zeichen und keine bekannten Marken oder Figuren."
            ),
        }, {
            "role": "user",
            "content": (
                f"Erstelle eine neue Idee im Genre {req.genre}. Es gibt {req.character_count} feste Figuren; "
                f"ihre Stimmen sind {gender_label}. Die Erzählstimme ist "
                f"{'weiblich' if req.narrator_gender == 'female' else 'männlich'}. "
                "Die Handlung muss zehn fortlaufende Bände mit je ungefähr 23 Minuten tragen können."
            ),
        }],
        model=req.model_name,
        temperature=0.9,
        max_tokens=320,
    )
    content = response["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
    try:
        idea = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(502, "Das Modell lieferte keine gültige Story-Idee. Bitte erneut versuchen.") from exc
    title = str(idea.get("title", "")).strip()
    premise = str(idea.get("premise", "")).strip()
    if not title or not premise:
        raise HTTPException(502, "Die generierte Story-Idee war unvollständig. Bitte erneut versuchen.")
    return {"title": title, "premise": premise, "genre": req.genre}


@router.post("/create")
async def create_story(req: CreateStoryRequest, _=Depends(verify_api_key)):
    session_id = str(uuid.uuid4())[:8]
    characters = _normalize_characters(
        req.characters or _generated_characters(
            req.narrator_gender, req.character_gender, req.character_count
        )
    )
    now = datetime.now(timezone.utc).isoformat()
    session = {
        "session_id": session_id,
        "title": req.title,
        "premise": req.premise,
        "genre": req.genre,
        "model_name": (
            "Qwen/Qwen3-4B-Instruct-2507-GGUF"
            if not req.model_name or "0.6B" in req.model_name or "1.7B" in req.model_name
            else req.model_name
        ),
        "characters": characters,
        "messages": [],
        "status": "idle",
        "current_scene": 0,
        "current_character_index": 0,
        "volume": 1,
        # About 140 spoken words/minute. A scene contains one long narrator
        # passage plus one short passage per character.
        "band_minutes": req.band_minutes,
        "scenes_per_volume": max(3, round(
            req.band_minutes * 140 / (130 + 35 * req.character_count)
        )),
        "volume_script_ready": False,
        "volume_message_start": 0,
        "narration_index": 0,
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
    if session.get("status") == "finished" and session.get("volume", 1) >= 10:
        return {"status": "finished", "session_id": session_id, "series_finished": True}
    if session.get("status") == "finished" and session.get("volume_script_ready"):
        session["volume"] = session.get("volume", 1) + 1
        session["volume_script_ready"] = False
        session["narration_index"] = len(session.get("messages", []))
        session["volume_message_start"] = len(session.get("messages", []))
    if not session.get("model_name") or "0.6B" in session["model_name"] or "1.7B" in session["model_name"]:
        session["model_name"] = "Qwen/Qwen3-4B-Instruct-2507-GGUF"
        for character in session.get("characters", []):
            if character.model_name and ("0.6B" in character.model_name or "1.7B" in character.model_name):
                character.model_name = ""
        save_session("story", session)
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

    return EventSourceResponse(
        events(), ping=5,
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _ensure_story_voice(character: StoryCharacter) -> None:
    """Create once, then rebuild the exact same clone from persisted reference audio."""
    reference_text = "Ich spreche klar, natürlich und mit gleichmäßiger Betonung."

    def prepare_voice() -> tuple[str, str]:
        import base64
        import io

        import numpy as np
        import soundfile as sf

        reference_b64 = character.voice_reference_audio
        if reference_b64:
            reference_audio, sample_rate = sf.read(
                io.BytesIO(base64.b64decode(reference_b64)), dtype="float32"
            )
        else:
            designed, sample_rate = model_manager.get_voice_design_model().generate_voice_design(
                text=reference_text,
                language=character.language or "German",
                instruct=character.voice_description,
            )
            if isinstance(designed, (list, tuple)):
                designed = designed[0]
            reference_audio = np.asarray(designed, dtype=np.float32).squeeze()
            buffer = io.BytesIO()
            sf.write(buffer, reference_audio, sample_rate, format="WAV")
            reference_b64 = base64.b64encode(buffer.getvalue()).decode()

        prompt = model_manager.get_base_model().create_voice_clone_prompt(
            ref_audio=(reference_audio, sample_rate),
            ref_text=reference_text,
            x_vector_only_mode=True,
        )
        prompt_id = str(uuid.uuid4())[:8]
        store_voice_clone_prompt(prompt_id, {"prompt_items": prompt})
        return prompt_id, reference_b64

    character.voice_prompt_id, character.voice_reference_audio = await asyncio.to_thread(prepare_voice)


async def _story_loop(session_id: str):
    session = _sessions[session_id]
    queue = _queues[session_id]
    try:
        while session["status"] == "running" and session.get("volume", 1) <= 10:
            if not session.get("volume_script_ready"):
                # A stop during manuscript generation discards only the incomplete
                # current draft. Finished earlier volumes and narrated audio remain.
                draft_start = session.get("volume_message_start", session.get("narration_index", 0))
                session["messages"] = session["messages"][:draft_start]
                session["progress"] = {"percent": 3, "label": f"Band {_volume_label(session.get('volume', 1))}: Manuskript und feste Stimmen werden parallel vorbereitet"}
                save_session("story", session)
                await queue.put({"event": "progress", "data": session["progress"]})
                await asyncio.gather(_prepare_all_voices(session, queue), _write_volume(session, queue))
                session["volume_script_ready"] = True
                save_session("story", session)
            else:
                # In-memory clone prompts disappear on an application restart. The
                # persisted WAV references rebuild exactly the same voices.
                await _prepare_all_voices(session, queue)

            volume_start = session.get("volume_message_start", 0)
            volume_length = max(1, len(session["messages"]) - volume_start)
            while session["status"] == "running" and session["narration_index"] < len(session["messages"]):
                index = session["narration_index"]
                message = session["messages"][index]
                character = next(item for item in session["characters"] if item.id == message.speaker_id)
                percent = 50 + int(49 * (index - volume_start + 1) / volume_length)
                session["progress"] = {"percent": percent, "label": f"Band {_volume_label(session.get('volume', 1))}: {character.name} wird vertont"}
                save_session("story", session)
                await queue.put({"event": "progress", "data": session["progress"]})
                is_narrator = character.id == "narrator"
                tts_text = re.sub(r"([.!?…])\s+", r"\1 (pause) ", message.text)
                message.audio_base64 = await _tts_speech(
                    tts_text, character.voice_description, character.language,
                    voice_prompt_id=character.voice_prompt_id,
                    speed=0.76 if is_narrator else 0.84,
                    emotion_gap=0.42 if is_narrator else 0.30,
                    speaker=character.engine_voice,
                )
                session["narration_index"] = index + 1
                session["updated_at"] = datetime.now(timezone.utc).isoformat()
                save_session("story", session)
                await queue.put({"event": "message", "data": message.model_dump()})

            if session["status"] != "running":
                break
            if session.get("volume", 1) >= 10:
                session["status"] = "finished"
                session["progress"] = {"percent": 100, "label": "Band 1.9 und die gesamte Serie sind vollständig vertont"}
                await queue.put({"event": "status", "data": {"status": "finished"}})
                break

            session["volume"] += 1
            session["volume_script_ready"] = False
            session["volume_message_start"] = len(session["messages"])
            session["narration_index"] = len(session["messages"])
            session["progress"] = {"percent": 0, "label": f"Band {_volume_label(session['volume'])} startet automatisch"}
            save_session("story", session)
            await queue.put({"event": "progress", "data": session["progress"]})
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


async def _prepare_all_voices(session: dict, queue: asyncio.Queue) -> None:
    c_engine_ready = settings.tts_engine in {"c-server", "rust-server"} and await c_tts_is_healthy()
    for character in session["characters"]:
        if c_engine_ready:
            character.engine_voice = character.engine_voice or stable_preset(
                character.voice_description,
                female="frau" in character.voice_description.lower(),
            )
            continue
        if character.voice_prompt_id and get_voice_clone_prompt(character.voice_prompt_id) is not None:
            continue
        await queue.put({"event": "progress", "data": {
            "percent": 10, "label": f"Feste Stimme für {character.name} wird einmalig gebaut",
        }})
        await _ensure_story_voice(character)
        save_session("story", session)


async def _write_volume(session: dict, queue: asyncio.Queue) -> None:
    volume = session.get("volume", 1)
    first_scene = session.get("current_scene", 0) + 1
    last_scene = first_scene + session.get("scenes_per_volume", 5) - 1
    session["volume_last_scene"] = last_scene
    for scene in range(first_scene, last_scene + 1):
        for character in session["characters"]:
            if session["status"] != "running":
                raise asyncio.CancelledError
            await queue.put({"event": "progress", "data": {
                "percent": 15 + int(30 * (scene - first_scene + 1) / max(1, last_scene - first_scene + 1)),
                "label": f"Band {_volume_label(volume)}: Szene {scene} für {character.name} wird geschrieben",
            }})
            message = StoryMessage(**await _generate_turn(session, character, scene, queue))
            session["messages"].append(message)
            add_memory(session["session_id"], "story", character.name, message.text)
            save_session("story", session)
        session["current_scene"] = scene
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_session("story", session)


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
    is_narrator = character.id == "narrator" or character.role.lower() in {"erzählerin", "erzähler"}
    volume = session.get("volume", 1)
    volume_last_scene = session.get("volume_last_scene", scene)
    is_volume_finale = scene == volume_last_scene and character.id == session["characters"][-1].id
    role_rules = (
        "Die Erzählerin beschreibt alles in der dritten Person. Sie ist niemals selbst Figur, "
        "sagt niemals 'ich' und spricht nicht stellvertretend für Mara oder Elias. Sie schildert "
        "sichtbare, konkrete Sims-artige Alltagshandlungen, Orte, Gegenstände, Bedürfnisse und Folgen."
        if is_narrator else
        f"{character.name} handelt nur als eigene Figur. Beschreibe eine konkrete Handlung und "
        "natürlichen Dialog; kontrolliere oder vertone keine andere Figur und nicht die Erzählerin."
    )
    length_rule = (
        "9 bis 14 eher kurze, abwechslungsreiche Sätze mit insgesamt 110 bis 170 Wörtern"
        if is_narrator else
        "1 bis 3 natürliche Sätze mit insgesamt 20 bis 50 Wörtern"
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
- Fasse den vorherigen Beitrag niemals zusammen und beschreibe dieselbe Handlung nicht aus einer zweiten Perspektive.
- Schreibe wie eine professionelle Hörbucherzählerin: bildhaft, ruhig, mit wechselndem Satzrhythmus,
  sinnlichen Einzelheiten und bewussten Spannungsmomenten. Keine stakkatoartige Aufzählung von Handlungen.
- Jeder neue Absatz braucht ein anderes konkretes Ziel, Hindernis und Ergebnis als die bisherigen Absätze.
- Verrate nicht den gesamten Plot und beende die Geschichte nicht vorzeitig.
- {length_rule}, vollständig auf Deutsch.
- Schreibe eine vollständige, gehaltvolle Passage mit Handlung, Wahrnehmung und einer neuen Konsequenz.
- Erzählerin beschreibt nur in dritter Person; Figuren handeln und sprechen aus ihrer eigenen Perspektive.
- Gib ausschließlich den neuen Erzähltext aus: keine Sprecherbezeichnung, keine Wortzahl, keine Erklärung.
- Beginne jeden Beitrag mit genau einem passenden TTS-Emotions-Tag. Erlaubt sind unter anderem
  (calm), (warm), (thoughtful), (tense), (fearful), (surprised), (sad), (angry),
  (relieved), (whispering), (excited) und (serious).
- Verwende für dramatische Gefühle eine deutlich hörbare Intensität von 0.7 bis 0.95, zum Beispiel
  (fearful:0.85), (angry:0.8), (tense:0.9) oder (relieved:0.75). Starke Gefühle dürfen nicht neutral klingen.
- Wechsle die Emotion nur bei einem echten Stimmungsumschwung und setze den neuen Tag direkt vor
  den betreffenden Satz. Verwende ausdrucksstarke Interpunktion und natürliche Sprechpausen.
- Die Emotion muss aus der konkreten Situation entstehen; nicht dauerhaft neutral oder ruhig sprechen."""
    if is_volume_finale and volume == 10:
        system += "\n- Dies ist das vorbereitete Serienfinale in Band 10: Löse den zentralen Konflikt und die wichtigsten Figurenbögen endgültig, emotional und glaubwürdig auf. Kein Cliffhanger."
    elif is_volume_finale and volume >= 5:
        system += "\n- Beende diesen Band mit einem starken, konkreten Cliffhanger: eine unumkehrbare Entdeckung, Entscheidung oder unmittelbare Gefahr. Löse ihn noch nicht auf."
    elif is_volume_finale:
        system += "\n- Schließe den Hauptkonflikt dieses Bandes glaubwürdig ab, lasse aber einen subtilen neuen Ansatz für die spätere Fortsetzung offen."
    turn_instruction = (
        f"Szene {scene}, Erzählerin. Beschreibe in dritter Person eine längere zusammenhängende Passage. "
        "Greife das letzte konkrete Detail auf, führe es aber zu einer neuen Entdeckung oder Konsequenz. "
        "Keine Dialogwiederholung. Erzähle ruhig und atmosphärisch in 9 bis 14 eher kurzen Sätzen und 110 bis 170 Wörtern."
        if is_narrator else
        f"Szene {scene}, Fokusfigur {character.name}. Nur {character.name} handelt oder spricht. "
        "Reagiere auf den unmittelbar letzten Beitrag und treibe die Handlung mit einer neuen Entscheidung voran. "
        "Schreibe nur 1 bis 3 Sätze und 20 bis 50 Wörter."
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": turn_instruction}]
    text = ""
    session["progress"] = {"percent": 30, "label": f"Text für {character.name} wird geschrieben"}
    save_session("story", session)
    recent_tokens = [set(re.findall(r"[a-zäöüß]{4,}", item.text.lower())) for item in recent[-6:]]
    previous_words = re.findall(r"[a-zäöüß]+", " ".join(item.text.lower() for item in recent[-14:]))
    previous_ngrams = {tuple(previous_words[i:i + 6]) for i in range(max(0, len(previous_words) - 5))}
    for attempt in range(3):
        response = await get_lm_studio_client().chat_completion(
            messages, model=character.model_name or session["model_name"],
            temperature=0.68 + attempt * 0.06, max_tokens=420,
        )
        text = response["choices"][0]["message"]["content"].strip()
        text = re.sub(r"^(?:Erzählerin|Mara|Elias)\s*[:.\-—]\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*\(\s*\d+\s+Wörter\s*\)\s*$", "", text, flags=re.IGNORECASE)
        tokens = set(re.findall(r"[a-zäöüß]{4,}", text.lower()))
        words = re.findall(r"\b[\wÄÖÜäöüß'-]+\b", text)
        similarity = max((len(tokens & old) / max(1, len(tokens | old)) for old in recent_tokens), default=0)
        repeated_quote = any(
            quote.lower() in " ".join(item.text.lower() for item in recent[-6:])
            for quote in re.findall(r"[„\"]([^“\"]+)[“\"]", text)
        )
        wrong_role = (
            (is_narrator and bool(re.search(r"\b(?:ich|mich|mein(?:e[rmns]?)?|mir|wir|uns)\b", text, re.IGNORECASE)))
            or (not is_narrator and bool(re.search(r"^Erzählerin\b", text, re.IGNORECASE)))
        )
        candidate_words = re.findall(r"[a-zäöüß]+", text.lower())
        repeats_phrase = any(
            tuple(candidate_words[i:i + 6]) in previous_ngrams
            for i in range(max(0, len(candidate_words) - 5))
        )
        sentence_count = len(re.findall(r"[.!?…](?:\s|$)", text))
        min_words, max_words = (85, 180) if is_narrator else (15, 60)
        enough_sentences = sentence_count >= 7 if is_narrator else sentence_count >= 1
        if min_words <= len(words) <= max_words and enough_sentences and similarity < 0.45 and not repeats_phrase and not repeated_quote and not wrong_role:
            break
        messages.append({"role": "assistant", "content": text})
        retry_length = "9 bis 14 kurze Sätze und 110 bis 170 Wörter" if is_narrator else "1 bis 3 Sätze und 20 bis 50 Wörter"
        messages.append({"role": "user", "content": f"Der Entwurf klingt wiederholend oder heruntergerasselt. Schreibe eine wirklich neue Passage mit {retry_length}: neue Handlung, neue Bilder, andere Satzanfänge und keine bekannte Sechs-Wort-Folge oder Dialogzeile."})
    if progress_queue is not None:
        preview = {
            "speaker_id": character.id, "speaker_name": character.name,
            "text": text, "audio_base64": None,
            "timestamp": datetime.now(timezone.utc).isoformat(), "scene": scene,
        }
        await progress_queue.put({"event": "text", "data": preview})
    return {
        "speaker_id": character.id,
        "speaker_name": character.name,
        "text": text,
        "audio_base64": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scene": scene,
    }


def _state(session: dict) -> dict:
    state = {
        key: value for key, value in session.items()
        if key != "running_task"
    }
    state["characters"] = [
        character.model_dump(exclude={"voice_reference_audio"})
        if hasattr(character, "model_dump") else {
            key: value for key, value in character.items()
            if key != "voice_reference_audio"
        }
        for character in session.get("characters", [])
    ]
    return state
