
"""Story candidate ranking, local semantic embeddings, editor and audio direction."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass

from app.services.story_model_runtime import story_chat_completion

_DIM = 384
_AUDIO_TAG = re.compile(r"\[(?:sfx|ambience|silence):[^\]]+\]", re.IGNORECASE)
_EMOTION_TAG = re.compile(r"\((?:calm|warm|thoughtful|tense|fearful|surprised|sad|angry|relieved|whispering|excited|serious)(?::0?\.\d+)?\)", re.IGNORECASE)


@dataclass
class RankedCandidate:
    text: str
    score: float
    semantic_similarity: float
    reasons: list[str]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zäöüß]{3,}", text.lower())


def embed_text(text: str) -> list[float]:
    vector = [0.0] * _DIM
    tokens = _tokens(text)
    for token in tokens:
        for feature in (token, *(token[i:i + 3] for i in range(max(0, len(token) - 2)))):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % _DIM
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(left * right for left, right in zip(a, b))


def rank_candidates(candidates: list[str], history: list[str], is_narrator: bool) -> list[RankedCandidate]:
    history_vectors = [embed_text(item) for item in history[-24:] if item.strip()]
    ranked: list[RankedCandidate] = []
    for text in candidates:
        vector = embed_text(text)
        similarity = max((cosine(vector, old) for old in history_vectors), default=0.0)
        words = _tokens(_AUDIO_TAG.sub("", _EMOTION_TAG.sub("", text)))
        sentences = len(re.findall(r"[.!?…](?:\s|$)", text))
        novelty = max(0.0, 1.0 - max(0.0, similarity))
        length_target = (110, 170) if is_narrator else (20, 50)
        length_score = 1.0 if length_target[0] <= len(words) <= length_target[1] else 0.45
        sentence_score = 1.0 if sentences >= (7 if is_narrator else 1) else 0.35
        emotion_score = 1.0 if _EMOTION_TAG.search(text) else 0.4
        score = novelty * 52.0 + length_score * 18.0 + sentence_score * 15.0 + emotion_score * 15.0
        reasons = []
        if similarity > 0.72:
            reasons.append("semantisch zu ähnlich zu früheren Szenen")
        if length_score < 1.0:
            reasons.append("Länge außerhalb des Zielbereichs")
        if emotion_score < 1.0:
            reasons.append("kein gültiges Emotions-Tag")
        ranked.append(RankedCandidate(text=text, score=round(score, 2), semantic_similarity=round(similarity, 4), reasons=reasons))
    return sorted(ranked, key=lambda item: item.score, reverse=True)


async def edit_and_direct(
    ranked: list[RankedCandidate],
    history: list[str],
    story_context: str,
    model: str,
    is_narrator: bool,
) -> str:
    best = ranked[0]
    alternatives = "\n\n".join(
        f"Kandidat {index + 1}, lokale Punktzahl {item.score}, Ähnlichkeit {item.semantic_similarity}:\n{item.text}"
        for index, item in enumerate(ranked)
    )
    recent = "\n".join(history[-10:])
    role = "Erzählerin" if is_narrator else "Figur"
    prompt = f"""Du bist strenger Story-Editor, Kontinuitätsprüfer, Ranking-Modell und Audio-Regisseur.
Bearbeite eine fortlaufende deutsche Hörspielserie. Die Bände bauen zwingend aufeinander auf.

STORY-KONTEXT:
{story_context}

LETZTE SZENEN:
{recent}

KANDIDATEN:
{alternatives}

AUFGABE:
1. Wähle den logisch besten Kandidaten.
2. Korrigiere Kontinuität, Ursache-Wirkung, Figurenwissen und Wiederholungen.
3. Erhalte offene Handlungsfäden; löse nichts zu früh auf.
4. Gib nur die endgültige Passage aus, keine Analyse.
5. Beginne mit einem passenden Emotions-Tag.
6. Setze Audio-Regie-Tags nur an sinnvollen Stellen:
   [ambience:station], [ambience:rain], [sfx:train_arriving],
   [sfx:thunder], [silence:700ms].
7. Die Tags dürfen niemals gesprochen werden. Weniger ist besser als unpassender Lärm.
8. Bei Perspektivwechsel zuerst Ort oder Ereignis hörbar etablieren, dann {role} sprechen lassen.
9. Keine bekannten Formulierungen oder sinngleichen Wiederholungen aus den letzten Szenen.
"""
    response = await story_chat_completion(
        "editor",
        [{"role": "system", "content": prompt}, {"role": "user", "content": "Erzeuge die endgültige, vertonungsfertige Passage."}],
        temperature=0.2,
        max_tokens=520,
    )
    edited = response["choices"][0]["message"]["content"].strip()
    edited = re.sub(r"^(?:Endfassung|Finale Fassung|Ausgabe)\s*[:\-]\s*", "", edited, flags=re.IGNORECASE)
    if not _EMOTION_TAG.search(edited):
        edited = "(serious) " + edited
    return edited
