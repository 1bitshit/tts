"""
Inline emotion tag parser for fine-grained expressive TTS control.

Parses tags like (happy), (sad:0.8), (whispering)(tense) from text and
converts them to instruct-based segments for the Qwen3-TTS model.

Tag syntax:
  (emotion)              — basic emotion
  (emotion:intensity)    — emotion with intensity 0.0-1.0
  (tone)                 — speaking tone
  (effect)               — vocal effect

Tags can be stacked: (laughing)(happy) Hello!
"""
import io
import logging
import re
from typing import Callable, List, Optional, Tuple

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

EMOTION_TAGS = {
    "happy": "Speak in a happy, cheerful tone with a bright voice.",
    "sad": "Speak in a sad, melancholic tone with a subdued voice.",
    "angry": "Speak in an angry, agitated tone with a raised voice.",
    "fearful": "Speak in a fearful, anxious tone with a trembling voice.",
    "surprised": "Speak in a surprised, astonished tone.",
    "calm": "Speak in a calm, relaxed tone with a steady voice.",
    "neutral": "",
    "excited": "Speak in an excited, enthusiastic tone with high energy.",
    "joyful": "Speak in a joyful, delighted tone.",
    "playful": "Speak in a playful, teasing tone.",
    "sarcastic": "Speak in a sarcastic, ironic tone.",
    "crying": "Speak with a crying, tearful voice, as if holding back sobs.",
    "whispering": "Speak in a quiet whisper, as if sharing a secret.",
    "shouting": "Speak loudly, as if shouting across a distance.",
    "laughing": "Speak with laughter in your voice, amused.",
    "breathless": "Speak breathlessly, as if out of breath.",
    "tense": "Speak with tension in your voice, strained.",
    "relieved": "Speak with relief, as if a weight has been lifted.",
    "mock_angry": "Speak with mock anger, playful frustration.",
    "soft": "Speak softly and gently.",
    "serious": "Speak in a serious, grave tone.",
    "romantic": "Speak in a romantic, tender tone.",
    "confident": "Speak confidently and assertively.",
    "confused": "Speak with confusion and uncertainty.",
    "bored": "Speak in a bored, disinterested tone.",
    "thoughtful": "Speak thoughtfully, as if pondering.",
    "warm": "Speak with warmth and kindness.",
    "cold": "Speak coldly and distantly.",
    "formal": "Speak in a formal, proper manner.",
    "casual": "Speak casually and informally.",
}

TONE_TAGS = {
    "fast": "Speak quickly, at a faster pace.",
    "slow": "Speak slowly, at a relaxed pace.",
    "pause": "",
    "long_pause": "",
    "sigh": "[sighs deeply] ",
    "breath": "[takes a breath] ",
}

TAG_PATTERN = re.compile(
    r"\(("
    + "|".join(re.escape(t) for t in list(EMOTION_TAGS.keys()) + list(TONE_TAGS.keys()))
    + r")(?::(\d*\.?\d+))?\)"
)


def parse_emotion_tags(text: str) -> List[Tuple[str, str]]:
    segments: List[Tuple[str, str]] = []
    current_instructions: List[str] = []
    last_end = 0

    for match in TAG_PATTERN.finditer(text):
        tag_start = match.start()
        if tag_start > last_end:
            raw_text = text[last_end:tag_start].strip()
            if raw_text:
                instruct = ". ".join(current_instructions) if current_instructions else ""
                segments.append((instruct, raw_text))

        tag_name = match.group(1)
        intensity = match.group(2)

        if tag_name in EMOTION_TAGS:
            base = EMOTION_TAGS[tag_name]
            if intensity:
                base += f" (intensity: {intensity})"
            current_instructions = [base] if base else []
        elif tag_name in TONE_TAGS:
            base = TONE_TAGS[tag_name]
            if base:
                current_instructions.append(base)

        last_end = match.end()

    if last_end < len(text):
        remaining = text[last_end:].strip()
        if remaining:
            instruct = ". ".join(current_instructions) if current_instructions else ""
            segments.append((instruct, remaining))

    if not segments:
        return [("", text)]

    return segments


def has_emotion_tags(text: str) -> bool:
    return bool(TAG_PATTERN.search(text))


def strip_emotion_tags(text: str) -> str:
    return TAG_PATTERN.sub("").strip()


def format_emotion_text(segments: List[Tuple[str, str]]) -> str:
    return " ".join(t for _, t in segments)


# ── Multi-segment TTS generation with emotion tags ──────────────────

def generate_with_emotion_tags(
    text: str,
    generate_func: Callable,
    base_instruct: str = "",
    **kwargs,
) -> Tuple[np.ndarray, int]:
    """
    Generate TTS audio with inline emotion tag support.

    Args:
        text: Input text with optional emotion tags
        generate_func: Callable(seg_instruct, seg_text, **kwargs) -> (audio_array, sample_rate)
        base_instruct: Base instruction prepended to each segment
        **kwargs: Additional kwargs passed to generate_func

    Returns:
        (concatenated_audio, sample_rate)
    """
    sr = kwargs.get("sr", 24000)
    gap_seconds = float(kwargs.pop("gap_seconds", 0.15))

    def normalize(result: tuple) -> Tuple[np.ndarray, int]:
        audio, sample_rate = result
        if isinstance(audio, (list, tuple)):
            arrays = [np.asarray(item).squeeze() for item in audio if np.asarray(item).size]
            audio = np.concatenate(arrays) if len(arrays) > 1 else arrays[0]
        audio = np.asarray(audio, dtype=np.float32).squeeze()
        if audio.ndim != 1:
            raise ValueError(f"TTS returned unsupported audio shape {audio.shape}")
        return audio, int(sample_rate)

    if not has_emotion_tags(text):
        return normalize(generate_func(base_instruct, text, **kwargs))

    segments = parse_emotion_tags(text)
    all_audio = []
    total_samples = 0

    for seg_instruct, seg_text in segments:
        combined = base_instruct
        if seg_instruct:
            combined = f"{base_instruct}. {seg_instruct}" if base_instruct else seg_instruct

        if not seg_text.strip():
            continue

        try:
            audio, segment_sr = normalize(generate_func(combined, seg_text, **kwargs))
            if segment_sr != sr:
                import librosa
                audio = librosa.resample(audio, orig_sr=segment_sr, target_sr=sr)
            all_audio.append(audio)
            total_samples += len(audio)
            # Add small gap between segments
            gap = np.zeros(int(sr * gap_seconds), dtype=audio.dtype)
            all_audio.append(gap)
            total_samples += len(gap)
        except Exception as e:
            logger.warning(f"Emotion segment TTS failed: {e}")
            continue

    if not all_audio:
        return normalize(generate_func(base_instruct, strip_emotion_tags(text), **kwargs))

    result = np.concatenate(all_audio)
    return result, sr
