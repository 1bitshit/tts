"""
Health check endpoints
"""
from fastapi import APIRouter
from app.models.schemas import HealthResponse, ModelsHealthResponse
from app.models.manager import model_manager
from app import __version__
from app.config import settings
from app.services.c_tts import is_healthy as c_tts_is_healthy

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Basic health check endpoint

    Returns service status and version
    """
    return HealthResponse(
        status="healthy",
        version=__version__
    )


@router.get("/health/models", response_model=ModelsHealthResponse)
async def models_health_check():
    """
    Check which models are currently loaded

    Returns status of all model types
    """
    return ModelsHealthResponse(
        custom_voice_loaded=model_manager.is_loaded("custom_voice"),
        voice_design_loaded=model_manager.is_loaded("voice_design"),
        base_loaded=model_manager.is_loaded("base"),
        tokenizer_loaded=True  # Tokenizer is part of model loading
    )


@router.get("/health/tts")
async def tts_engine_health_check():
    healthy = True if settings.tts_engine == "python" else await c_tts_is_healthy()
    return {
        "status": "healthy" if healthy else "unavailable",
        "engine": settings.tts_engine,
        "url": settings.c_tts_url if settings.tts_engine in {"c-server", "rust-server"} else None,
        "app_version": __version__,
    }
