"""FastAPI 应用入口。"""
from contextlib import asynccontextmanager
import subprocess
import time
import uuid

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, StreamingResponse

from app.config import PRODUCT_DEFAULT_MODEL, SERVER_BOOT_ID, settings
from app.services import audit_db
from app.services.audit_log import (
    extract_audit_context,
    parse_request_body_for_audit,
    parse_response_body_for_audit,
    schedule_record_http_request,
)
from app.api.routes import agent, chat, config, data, export, health, load, plan, sessions
from app.services.llm import (
    LLM_HTTP_LIMITS,
    create_llm_http_client,
    set_shared_llm_http_client,
)
from app.logging_config import (
    get_logger,
    init_logging,
    log_exception_traceback,
    reset_trace_id,
    set_trace_id,
)

init_logging()
logger = get_logger("app.main")

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
        logger.info("AUTO_START_OLLAMA=False，跳过自动启动 Ollama")
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
    logger.info(
        "app startup title=%s server_boot_id=%s auto_start_ollama=%s ollama_base=%s",
        settings.APP_TITLE,
        SERVER_BOOT_ID,
        settings.AUTO_START_OLLAMA,
        settings.OLLAMA_BASE,
    )
    if settings.OPENROUTER_MODEL != PRODUCT_DEFAULT_MODEL:
        logger.warning(
            "running non-default model: OPENROUTER_MODEL=%s (product default=%s)",
            settings.OPENROUTER_MODEL,
            PRODUCT_DEFAULT_MODEL,
        )
    _start_ollama()
    llm_http_client = create_llm_http_client()
    set_shared_llm_http_client(llm_http_client)
    logger.info(
        "llm httpx AsyncClient started max_connections=%s max_keepalive=%s keepalive_expiry=%s",
        LLM_HTTP_LIMITS.max_connections,
        LLM_HTTP_LIMITS.max_keepalive_connections,
        LLM_HTTP_LIMITS.keepalive_expiry,
    )
    await audit_db.init_audit_db()
    try:
        yield
    finally:
        logger.info("app shutdown")
        await audit_db.close_audit_db()
        set_shared_llm_http_client(None)
        await llm_http_client.aclose()
        logger.info("llm httpx AsyncClient closed")
        _stop_ollama_if_started()


app = FastAPI(title=settings.APP_TITLE, lifespan=lifespan)

http_logger = get_logger("http")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """记录请求起止、注入 X-Request-ID、捕获审计字段并异步落库。"""

    async def dispatch(self, request: Request, call_next):
        raw = request.headers.get("X-Request-ID")
        trace_id = (raw.strip() if raw else "") or uuid.uuid4().hex
        token = set_trace_id(trace_id)
        start = time.perf_counter()
        path = request.url.path
        client_host = request.client.host if request.client else "-"
        ua = (request.headers.get("user-agent") or "")[:120]
        http_logger.info(
            "request start method=%s path=%s client=%s ua=%s",
            request.method,
            path,
            client_host,
            ua,
        )

        body_bytes = await request.body()

        async def receive():
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        replay_request = Request(request.scope, receive)
        req_content_type = request.headers.get("content-type")
        req_body_audit = parse_request_body_for_audit(
            body_bytes,
            path=path,
            content_type=req_content_type,
        )
        audit_ctx = extract_audit_context(replay_request, body=req_body_audit, path=path)

        response_status: int | None = None
        response_body_audit: object | None = None
        error_detail: str | None = None
        is_streaming = False

        try:
            response = await call_next(replay_request)
            response.headers["X-Request-ID"] = trace_id
            response_status = response.status_code
            is_streaming = isinstance(response, StreamingResponse)
            if is_streaming:
                response_body_audit = parse_response_body_for_audit(
                    b"",
                    path=path,
                    content_type=response.headers.get("content-type"),
                    is_streaming=True,
                )
            else:
                chunks: list[bytes] = []
                async for chunk in response.body_iterator:
                    chunks.append(chunk)
                resp_bytes = b"".join(chunks)
                response_body_audit = parse_response_body_for_audit(
                    resp_bytes,
                    path=path,
                    content_type=response.headers.get("content-type"),
                    is_streaming=False,
                )
                response = Response(
                    content=resp_bytes,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            elapsed_ms = (time.perf_counter() - start) * 1000
            http_logger.info(
                "request end status=%s elapsed_ms=%.2f error=%s",
                response_status,
                elapsed_ms,
                response_status >= 400,
            )
            return response
        except HTTPException as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            response_status = exc.status_code
            error_detail = str(exc.detail)
            http_logger.warning(
                "request HTTPException status=%s elapsed_ms=%.2f path=%s",
                exc.status_code,
                elapsed_ms,
                path,
            )
            raise
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            response_status = 500
            error_detail = str(exc)
            http_logger.exception("request failed elapsed_ms=%.2f", elapsed_ms)
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            schedule_record_http_request(
                trace_id=trace_id,
                method=request.method,
                path=path,
                query_params=dict(request.query_params),
                request_body=req_body_audit,
                response_status=response_status,
                response_body=response_body_audit,
                error_detail=error_detail,
                duration_ms=elapsed_ms,
                client_host=client_host,
                project_id=audit_ctx.get("project_id"),
                session_id=audit_ctx.get("session_id"),
                workspace_key_hash=audit_ctx.get("workspace_key_hash"),
                workspace_kind=audit_ctx.get("workspace_kind"),
                model_tag=audit_ctx.get("model_tag"),
                request_kind=audit_ctx.get("request_kind"),
            )
            reset_trace_id(token)


# 先于 CORS 注册，使外层最后执行（Starlette 后添加的先处理）
app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """记录 HTTP 异常并返回与 FastAPI 一致的 JSON 形状。"""
    if exc.status_code >= 500:
        http_logger.error(
            "HTTPException status=%s detail=%s path=%s",
            exc.status_code,
            exc.detail,
            request.url.path,
        )
    else:
        http_logger.warning(
            "HTTPException status=%s detail=%s path=%s",
            exc.status_code,
            exc.detail,
            request.url.path,
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """记录未捕获异常，避免静默 500。"""
    if log_exception_traceback():
        http_logger.exception("unhandled exception path=%s", request.url.path)
    else:
        http_logger.error(
            "unhandled exception path=%s err=%s",
            request.url.path,
            exc,
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


app.include_router(config.router)
app.include_router(health.router)
app.include_router(plan.router)
app.include_router(agent.router)
app.include_router(export.router)
app.include_router(load.router)
app.include_router(data.router)
app.include_router(chat.router)
app.include_router(sessions.router)
