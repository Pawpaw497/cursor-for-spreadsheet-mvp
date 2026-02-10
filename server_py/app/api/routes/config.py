"""配置与模型选项 API，供前端切换模型使用。"""
from fastapi import APIRouter

from app.config import settings

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
async def get_config():
    """返回当前模型配置及可选模型列表（id + label）。"""
    return {
        "openRouterModel": settings.OPENROUTER_MODEL,
        "openRouterModels": [{"id": mid, "label": label} for mid, label in settings.openrouter_model_list],
        "ollamaModel": settings.OLLAMA_MODEL,
        "ollamaModels": [{"id": mid, "label": label} for mid, label in settings.ollama_model_list],
    }
