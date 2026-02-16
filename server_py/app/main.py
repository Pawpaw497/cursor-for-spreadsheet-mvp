"""FastAPI 应用入口。"""
from contextlib import asynccontextmanager
import logging
import subprocess
import time

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.routes import agent, config, export, health, plan

logger = logging.getLogger(__name__)

_ollama_process: subprocess.Popen | None = None


def _ollama_is_running() -> bool:
    """检查 Ollama 服务是否已运行。"""
    try:
        r = httpx.get(f"{settings.OLLAMA_BASE}/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _start_ollama() -> bool:
    """若 Ollama 未运行且启用了自动启动，则启动 ollama serve。返回是否由本进程启动。"""
    global _ollama_process
    if not settings.AUTO_START_OLLAMA:
        return False
    if _ollama_is_running():
        logger.info("Ollama 已在运行，跳过自动启动")
        return False
    try:
        # ollama serve 需在 PATH 中（安装 Ollama 后通常可用）
        _ollama_process = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("已启动 ollama serve（PID=%s），等待就绪…", _ollama_process.pid)
        for _ in range(30):
            time.sleep(0.5)
            if _ollama_is_running():
                logger.info("Ollama 已就绪")
                return True
        logger.warning("Ollama 启动超时，本地模型可能暂不可用")
        return True  # 进程已启动，可能仍在初始化
    except FileNotFoundError:
        logger.warning("未找到 ollama 命令，请先安装 Ollama: https://ollama.ai")
        return False
    except Exception as e:
        logger.exception("启动 Ollama 失败: %s", e)
        return False


def _stop_ollama_if_started() -> None:
    """若由本进程启动了 Ollama，则终止。"""
    global _ollama_process
    if _ollama_process is not None:
        try:
            _ollama_process.terminate()
            _ollama_process.wait(timeout=5)
        except Exception:
            _ollama_process.kill()
        _ollama_process = None
        logger.info("已停止由本进程启动的 Ollama")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时尝试启动本地 Ollama，关闭时清理。"""
    _start_ollama()
    yield
    _stop_ollama_if_started()


app = FastAPI(title=settings.APP_TITLE, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config.router)
app.include_router(health.router)
app.include_router(plan.router)
app.include_router(agent.router)
app.include_router(export.router)
