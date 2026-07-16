"""Serialize LM Studio model swaps and inference for the story pipeline."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.lm_studio import get_lm_studio_client

logger = logging.getLogger(__name__)
_LOCK = asyncio.Lock()
_ACTIVE_ROLE: str | None = None
_SCRIPT = Path(__file__).resolve().parents[2] / "setup" / "story-models.sh"


def model_for_role(role: str) -> str:
    if role == "author":
        return settings.story_author_model
    if role == "editor":
        return settings.story_editor_model
    raise ValueError(f"Unknown story model role: {role}")


async def _load_role_locked(role: str) -> str:
    global _ACTIVE_ROLE
    if not settings.story_model_switching:
        return model_for_role(role)
    if _ACTIVE_ROLE == role:
        return f"story-{role}"
    process = await asyncio.create_subprocess_exec(
        "bash", str(_SCRIPT), "load", role,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"Story model switch to {role} failed: "
            f"{stderr.decode(errors='replace')[-1200:]}"
        )
    _ACTIVE_ROLE = role
    logger.info("Story model active: %s (%s)", role, stdout.decode().strip())
    return f"story-{role}"


async def story_chat_completion(
    role: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    async with _LOCK:
        model = await _load_role_locked(role)
        return await get_lm_studio_client().chat_completion(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
