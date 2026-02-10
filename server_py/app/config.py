"""应用配置，从环境变量加载。"""
import os

from dotenv import load_dotenv

load_dotenv()


def _parse_model_options(ids_env: str, labels_env: str, default_id: str) -> list[tuple[str, str]]:
    """解析逗号分隔的模型 id 与 label，返回 [(id, label), ...]。"""
    ids = [s.strip() for s in (ids_env or "").split(",") if s.strip()]
    labels = [s.strip() for s in (labels_env or "").split(",") if s.strip()]
    if not ids:
        return [(default_id, default_id)]
    return [(mid, labels[i] if i < len(labels) else mid) for i, mid in enumerate(ids)]


class Settings:
    """应用配置项。"""

    # LLM - Cloud (OpenRouter)
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openrouter/auto")
    OPENROUTER_MODELS: str = os.getenv(
        "OPENROUTER_MODELS",
        "openrouter/auto,openrouter/anthropic/claude-3.5-sonnet,openrouter/google/gemini-2.0-flash-001"
    )
    OPENROUTER_LABELS: str = os.getenv(
        "OPENROUTER_LABELS",
        "Auto,Qwen,Gemini"
    )

    # LLM - Local (Ollama)
    OLLAMA_BASE: str = os.getenv(
        "OLLAMA_BASE", "http://localhost:11434").rstrip("/")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    OLLAMA_MODELS: str = os.getenv("OLLAMA_MODELS", "qwen2.5:7b")
    OLLAMA_LABELS: str = os.getenv("OLLAMA_LABELS", "qwen2.5:7b")

    # App
    APP_TITLE: str = "Cursor for Spreadsheet (Python Server)"

    @property
    def openrouter_model_list(self) -> list[tuple[str, str]]:
        return _parse_model_options(
            self.OPENROUTER_MODELS, self.OPENROUTER_LABELS, "openrouter/auto"
        )

    @property
    def ollama_model_list(self) -> list[tuple[str, str]]:
        return _parse_model_options(
            self.OLLAMA_MODELS, self.OLLAMA_LABELS, "qwen2.5:7b"
        )


settings = Settings()
