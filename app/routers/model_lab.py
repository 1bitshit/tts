"""Daily small-model recommendations, setup jobs, tests and ratings."""

import asyncio
import hashlib
import json
import re
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.services.lm_studio import get_lm_studio_client
from app.services.session_store import model_rating_summary, rate_model

router = APIRouter(prefix="/api/v1/model-lab", tags=["model-lab"])

CATALOG = {
    "debate": [
        {"id": "Qwen/Qwen3-0.6B-GGUF", "size": "0.6B", "tier": "fast", "source": "official", "reason": "Sehr klein, 100+ Sprachen, Reasoning und Multi-Turn."},
        {"id": "Qwen/Qwen3-1.7B-GGUF", "size": "1.7B", "tier": "balanced", "source": "official", "reason": "Bessere Argumentstruktur bei weiterhin kleinem Speicherbedarf."},
        {"id": "ggml-org/gemma-3-1b-it-GGUF", "size": "1B", "tier": "fast", "source": "community-quant", "reason": "140+ Sprachen und starke Instruktionsbefolgung."},
        {"id": "mradermacher/Qwen3-4b-modern-german-GGUF", "size": "4B", "tier": "quality", "reason": "Deutsch-Finetune; optional bei mehr VRAM."},
        {"id": "Qwen/Qwen3-4B-GGUF", "size": "4B", "tier": "quality", "reason": "Stärkeres logisches Reasoning und Rollenspiel."},
        {"id": "ggml-org/SmolLM3-3B-GGUF", "size": "3B", "tier": "quality", "reason": "Deutsch nativ unterstützt; gute kleine Alternative."},
    ],
    "story": [
        {"id": "Qwen/Qwen3-1.7B-GGUF", "size": "1.7B", "tier": "balanced", "source": "official", "reason": "Kreatives Schreiben, Rollenspiel und Multi-Turn."},
        {"id": "mradermacher/Qwen3-0.6B-CreativeWriting-GDPO-GGUF", "size": "0.8B", "tier": "experimental", "source": "community-finetune", "reason": "Kleiner Creative-Writing-GDPO-Finetune; erst testen und bewerten."},
        {"id": "mradermacher/Qwen2.5-1.5B-Creative-Writing-and-General-Tasks-Distilled-8Clusters-GGUF", "size": "1.5B", "tier": "experimental", "source": "community-finetune", "reason": "Creative-Writing-Distill im gewünschten Größenbereich."},
        {"id": "Qwen/Qwen3-0.6B-GGUF", "size": "0.6B", "tier": "fast", "source": "official", "reason": "Schnelle Figurenstimmen; RAG stabilisiert Kontinuität."},
        {"id": "ggml-org/gemma-3-1b-it-GGUF", "size": "1B", "tier": "fast", "source": "community-quant", "reason": "Großer multilingualer Kontext bei kleinem Modell."},
        {"id": "Qwen/Qwen3-4B-GGUF", "size": "4B", "tier": "quality", "reason": "Deutlich bessere kreative Langform und Rollenstabilität."},
        {"id": "ggml-org/SmolLM3-3B-GGUF", "size": "3B", "tier": "quality", "reason": "Gute deutsche Textqualität für Erzählerrollen."},
        {"id": "tensorblock/Mistral-Nemo-Instruct-2407-GGUF", "size": "12B", "tier": "large", "reason": "Sehr guter deutscher Langkontext, nur bei viel VRAM/RAM."},
    ],
}

_jobs: dict[str, dict] = {}
_model_id_pattern = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.@-]+$")


class ModelAction(BaseModel):
    model_id: str
    kind: str = Field(pattern="^(debate|story)$")


class RatingRequest(ModelAction):
    rating: int = Field(ge=1, le=5)
    note: str = Field(default="", max_length=1000)


def _daily_three(kind: str) -> list[dict]:
    models = CATALOG[kind]
    seed = int(hashlib.sha256(f"{date.today().isoformat()}:{kind}".encode()).hexdigest()[:8], 16)
    start = seed % len(models)
    chosen = [models[(start + offset) % len(models)] for offset in range(3)]
    return [{**model, "rating": model_rating_summary(model["id"], kind)} for model in chosen]


@router.get("/recommendations")
async def recommendations(_=Depends(verify_api_key)):
    return {
        "date": date.today().isoformat(),
        "debate": _daily_three("debate"),
        "story": _daily_three("story"),
    }


async def _download_job(job_id: str, model_id: str):
    _jobs[job_id]["status"] = "running"
    try:
        process = await asyncio.create_subprocess_exec(
            str(_lms_binary()), "get", f"https://huggingface.co/{model_id}", "--gguf", "--yes",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        _jobs[job_id].update({
            "status": "finished" if process.returncode == 0 else "failed",
            "exit_code": process.returncode,
            "output": output.decode(errors="replace")[-6000:],
        })
    except Exception as exc:
        _jobs[job_id].update({"status": "failed", "error": str(exc)})


def _lms_binary() -> Path:
    candidates = [
        Path.home() / ".lmstudio/bin/lms",
        Path("/notebooks/fakeroot/home/bkg/.lmstudio/bin/lms"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise HTTPException(503, "LM Studio CLI is not installed")


@router.post("/download")
async def download_model(req: ModelAction, tasks: BackgroundTasks, _=Depends(verify_api_key)):
    if not _model_id_pattern.fullmatch(req.model_id):
        raise HTTPException(400, "Invalid Hugging Face model ID")
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {"job_id": job_id, "model_id": req.model_id, "kind": req.kind, "status": "queued"}
    tasks.add_task(_download_job, job_id, req.model_id)
    return _jobs[job_id]


@router.get("/jobs/{job_id}")
async def job(job_id: str, _=Depends(verify_api_key)):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    return _jobs[job_id]


@router.post("/setup/{kind}")
async def setup_models(kind: str, tasks: BackgroundTasks, _=Depends(verify_api_key)):
    if kind not in CATALOG:
        raise HTTPException(404, "Unknown model kind")
    default = "Qwen/Qwen3-0.6B-GGUF" if kind == "debate" else "Qwen/Qwen3-1.7B-GGUF"
    return await download_model(ModelAction(model_id=default, kind=kind), tasks, _)


@router.post("/test")
async def test_model(req: ModelAction, _=Depends(verify_api_key)):
    prompt = (
        "Antworte in höchstens 140 Wörtern auf Deutsch. "
        + ("Vertrete eine klare Position zur Frage: Sollte KI-Unterricht Pflichtfach werden?"
           if req.kind == "debate" else
           "Schreibe eine originelle Szene: Eine Uhrmacherin entdeckt, dass eine Uhr Erinnerungen speichert.")
    )
    lm = get_lm_studio_client()
    response = await lm.chat_completion(
        [{"role": "system", "content": "Schreibe präzise, natürlich und ohne Floskeln auf Deutsch."},
         {"role": "user", "content": prompt}],
        model=req.model_id,
        temperature=0.8,
        max_tokens=300,
    )
    output = response["choices"][0]["message"]["content"].strip()
    judge_prompt = f"""Bewerte den folgenden deutschen Modelltext für {req.kind}.
Gib ausschließlich JSON aus: {{"german":1-5,"coherence":1-5,"originality":1-5,"repetition":1-5,"summary":"kurz"}}.
Bei repetition bedeutet 5: keine störende Wiederholung.
TEXT:
{output}"""
    try:
        judged = await lm.chat_completion(
            [{"role": "user", "content": judge_prompt}],
            model=settings.model_judge_name,
            temperature=0.1,
            max_tokens=180,
        )
        raw = judged["choices"][0]["message"]["content"].strip().removeprefix("```json").removesuffix("```").strip()
        evaluation = json.loads(raw)
    except Exception as exc:
        evaluation = {"summary": f"Automatische Bewertung nicht verfügbar: {exc}"}
    return {"model_id": req.model_id, "kind": req.kind, "prompt": prompt, "output": output, "evaluation": evaluation}


@router.post("/rate")
async def rate(req: RatingRequest, _=Depends(verify_api_key)):
    rate_model(req.model_id, req.kind, req.rating, req.note)
    return {"status": "saved", "rating": model_rating_summary(req.model_id, req.kind)}
