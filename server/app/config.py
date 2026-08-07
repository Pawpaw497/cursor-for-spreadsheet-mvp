"""应用配置，从环境变量加载。"""
import os
import uuid

from dotenv import load_dotenv

load_dotenv()

# Single-model product phase: the one model the product guarantees and optimizes for.
# Referenced by env defaults below, /api/config, and the eval CLI so there is one source of truth.
PRODUCT_DEFAULT_MODEL = "deepseek/deepseek-v4-pro"


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
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", PRODUCT_DEFAULT_MODEL)
    OPENROUTER_MODELS: str = os.getenv(
        "OPENROUTER_MODELS",
        PRODUCT_DEFAULT_MODEL,
    )
    OPENROUTER_LABELS: str = os.getenv(
        "OPENROUTER_LABELS",
        "DeepSeek: DeepSeek V4 Pro",
    )

    # LLM - Local (Ollama)
    AUTO_START_OLLAMA: bool = os.getenv(
        "AUTO_START_OLLAMA", "0"
    ).lower() in ("1", "true", "yes")
    OLLAMA_BASE: str = os.getenv(
        "OLLAMA_BASE",
        "http://localhost:11434",
    ).rstrip("/")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    OLLAMA_MODELS: str = os.getenv("OLLAMA_MODELS", "qwen2.5:7b")
    OLLAMA_LABELS: str = os.getenv("OLLAMA_LABELS", "qwen2.5:7b")

    # App
    APP_TITLE: str = "Cursor for Spreadsheet (Python Server)"

    # Chat history / agent transcripts
    AGENT_TRANSCRIPTS_DIR: str = os.getenv("AGENT_TRANSCRIPTS_DIR", "")

    # LLM debug NDJSON (empty = disabled)
    LLM_DEBUG_LOG_DIR: str = os.getenv("LLM_DEBUG_LOG_DIR", "")
    LLM_DEBUG_MAX_CHARS: int = int(os.getenv("LLM_DEBUG_MAX_CHARS", "50000"))

    # SQLite audit log (HTTP + LLM); disabled when AUDIT_DB_ENABLED=0
    AUDIT_DB_ENABLED: bool = os.getenv("AUDIT_DB_ENABLED", "1").lower() in (
        "1",
        "true",
        "yes",
    )
    AUDIT_DB_PATH: str = os.getenv("AUDIT_DB_PATH", "data/audit.sqlite3")
    AUDIT_MAX_BODY_CHARS: int = int(os.getenv("AUDIT_MAX_BODY_CHARS", "50000"))
    AUDIT_STORE_WORKSPACE_KEY: bool = os.getenv(
        "AUDIT_STORE_WORKSPACE_KEY", "0"
    ).lower() in ("1", "true", "yes")
    # Optional server session memory (Stage 6); shares SQLite file with audit
    SESSION_MEMORY_DB_ENABLED: bool = os.getenv(
        "SESSION_MEMORY_DB_ENABLED", "0"
    ).lower() in ("1", "true", "yes")
    SESSION_MEMORY_TTL_DAYS: int = int(os.getenv("SESSION_MEMORY_TTL_DAYS", "7"))

    # SQLite table row store (upload API + context analyzer)
    DATA_DB_PATH: str = os.getenv("DATA_DB_PATH", "data/tables.sqlite3")
    MAX_UPLOAD_ROWS: int = int(os.getenv("MAX_UPLOAD_ROWS", "50000"))
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    TABLE_TTL_HOURS: int = int(os.getenv("TABLE_TTL_HOURS", "24"))

    # Agent PA: debug-only — parse assistant text as Plan JSON when structured output missing
    AGENT_PA_PLAN_JSON_FALLBACK: bool = os.getenv(
        "AGENT_PA_PLAN_JSON_FALLBACK", "0"
    ).lower() in ("1", "true", "yes")

    @property
    def openrouter_model_list(self) -> list[tuple[str, str]]:
        return _parse_model_options(
            self.OPENROUTER_MODELS, self.OPENROUTER_LABELS, PRODUCT_DEFAULT_MODEL
        )

    @property
    def ollama_model_list(self) -> list[tuple[str, str]]:
        return _parse_model_options(
            self.OLLAMA_MODELS, self.OLLAMA_LABELS, "qwen2.5:7b"
        )


settings = Settings()

# 进程级 ID：uvicorn/FastAPI 每次启动生成一次，供前端界定「本次启动后端期间」。
SERVER_BOOT_ID: str = uuid.uuid4().hex
