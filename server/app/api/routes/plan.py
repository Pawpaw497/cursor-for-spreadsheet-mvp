"""Plan 相关路由。"""
import json
import time
from collections import Counter
from typing import Dict, List

from fastapi import APIRouter, HTTPException

from app.logging_config import get_logger
from app.models import (
    ExecutePlanRequest,
    ExecutePlanResponse,
    ExecuteProjectPlanRequest,
    Plan,
    PlanRequest,
    PlanResponse,
    ProjectPlanByIdRequest,
    ProjectPlanRequest,
)
from app.services.llm import call_llm
from app.services.plan_executor import (
    ApplyResult,
    ProjectApplyResult,
    SchemaCol,
    TableData,
    apply_plan,
    apply_project_plan,
)
from app.services.prompts import (
    Message,
    ProjectPrompt,
    SpreadsheetPrompt,
    extract_json,
)
from app.services.projects import project_store

router = APIRouter(prefix="/api", tags=["plan"])
log = get_logger("api.plan")


def _step_type_summary(plan: Plan) -> str:
    """将 Plan 中各 step 的 action 聚合为简短字符串，便于日志。"""
    if not plan.steps:
        return ""
    c = Counter(getattr(s, "action", "?") for s in plan.steps)
    return ",".join(f"{k}={v}" for k, v in sorted(c.items()))


def _tables_shape_summary(tables: Dict[str, TableData]) -> str:
    """多表行列统计摘要。"""
    parts = []
    for name, t in sorted(tables.items()):
        parts.append(f"{name}:r{len(t.rows)}c{len(t.schema)}")
    return ";".join(parts)


async def _parse_and_validate_plan(
    content: str,
    retry_user: str,
    system_prompt: str,
    model_source: str,
    *,
    cloud_model_id: str | None = None,
    local_model_id: str | None = None,
) -> Plan:
    """解析 LLM 返回的 JSON 并校验为 Plan，失败时重试一次。"""
    json_text = extract_json(content)
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as e:
        preview = (json_text[:200] + "…") if len(json_text) > 200 else json_text
        log.warning("plan json parse failed first_try err=%s preview=%s", e, preview)
        retry_messages = [Message.system(system_prompt), Message.user(retry_user)]
        try:
            content = await call_llm(
                model_source=model_source,
                messages=retry_messages,
                cloud_model_id=cloud_model_id,
                local_model_id=local_model_id,
            )
        except RuntimeError as e:
            raise _http_exception_from_runtime(e) from e
        json_text = extract_json(content)
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as e2:
            log.error(
                "plan json parse failed after_retry err=%s raw_preview=%s",
                e2,
                (content[:200] + "…") if len(content) > 200 else content,
            )
            raise HTTPException(
                status_code=500,
                detail=f"[500] Model did not return valid JSON: {e2}. Raw: {content}",
            )
    try:
        return Plan.model_validate(parsed)
    except Exception as e:
        log.error("plan validation failed err=%s", e)
        raise HTTPException(
            status_code=500,
            detail=f"[500] Plan validation failed: {e}. Raw: {parsed}",
        )


def _http_exception_from_runtime(e: RuntimeError) -> HTTPException:
    """将底层 RuntimeError 转换为 HTTPException。

    对云端 LLM 鉴权类错误返回更友好的中文提示，其余错误保持原有 502 行为。

    Args:
        e: 底层 RuntimeError。

    Returns:
        对应的 HTTPException 实例。
    """
    msg = str(e)
    # 约定：AUTH_ERROR 前缀由 app.services.llm._raise_openrouter_error 添加。
    is_auth_error = (
        "AUTH_ERROR:" in msg
        or "HTTP 401" in msg
        or "HTTP 403" in msg
        or '"code":401' in msg
        or '"code":403' in msg
    )
    if is_auth_error:
        detail = "[502] 云端 LLM 鉴权失败：请检查 OPENROUTER_API_KEY 是否正确配置，" \
                 "并在 OpenRouter 控制台确认该 Key 有效且未过期。" \
                 f"（技术详情：{msg}）"
        return HTTPException(status_code=502, detail=detail)

    return HTTPException(status_code=502, detail=f"[502] {msg}")


@router.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest):
    t0 = time.perf_counter()
    log.info(
        "plan_request mode=single_table model_source=%s prompt_len=%d schema_cols=%d sample_rows=%d",
        req.modelSource or "cloud",
        len(req.prompt or ""),
        len(req.schema_),
        len(req.sampleRows),
    )
    prompt = SpreadsheetPrompt()
    user_content = prompt.build_user_content(req.prompt, req.schema_, req.sampleRows)
    model_source = req.modelSource or "cloud"
    messages = prompt.single_turn_messages(user_content)
    try:
        content = await call_llm(
            model_source=model_source,
            messages=messages,
            cloud_model_id=req.cloudModelId,
            local_model_id=req.localModelId,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"[400] {e}")
    except RuntimeError as e:
        raise _http_exception_from_runtime(e)

    plan_obj = await _parse_and_validate_plan(
        content=content,
        retry_user=user_content + "\nReturn ONLY JSON.",
        system_prompt=prompt.system,
        model_source=model_source,
        cloud_model_id=req.cloudModelId,
        local_model_id=req.localModelId,
    )
    log.info(
        "plan_response mode=single_table steps=%d step_summary=%s elapsed_ms=%.2f",
        len(plan_obj.steps),
        _step_type_summary(plan_obj),
        (time.perf_counter() - t0) * 1000,
    )
    return PlanResponse(plan=plan_obj)


@router.post("/plan-project", response_model=PlanResponse)
async def plan_project(req: ProjectPlanRequest):
    """Project-level planning: accepts multiple tables and can generate join/create_table steps."""
    t0 = time.perf_counter()
    log.info(
        "plan_request mode=project_tables model_source=%s tables=%d prompt_len=%d",
        req.modelSource or "cloud",
        len(req.tables),
        len(req.prompt or ""),
    )
    tables_data = [t.model_dump() for t in req.tables]
    prompt = ProjectPrompt()
    user_content = prompt.build_user_content(req.prompt, tables_data)
    model_source = req.modelSource or "cloud"
    messages = prompt.single_turn_messages(user_content)
    try:
        content = await call_llm(
            model_source=model_source,
            messages=messages,
            cloud_model_id=req.cloudModelId,
            local_model_id=req.localModelId,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"[400] {e}")
    except RuntimeError as e:
        raise _http_exception_from_runtime(e)

    plan_obj = await _parse_and_validate_plan(
        content=content,
        retry_user=user_content + "\nReturn ONLY JSON.",
        system_prompt=prompt.system,
        model_source=model_source,
        cloud_model_id=req.cloudModelId,
        local_model_id=req.localModelId,
    )
    log.info(
        "plan_response mode=project_tables steps=%d step_summary=%s elapsed_ms=%.2f",
        len(plan_obj.steps),
        _step_type_summary(plan_obj),
        (time.perf_counter() - t0) * 1000,
    )
    return PlanResponse(plan=plan_obj)


@router.post("/execute-plan", response_model=ExecutePlanResponse)
async def execute_plan(req: ExecutePlanRequest) -> ExecutePlanResponse:
    """无状态执行 Plan：前端携带当前 tables 与 plan，后端返回执行结果。"""
    if not req.tables:
        raise HTTPException(status_code=400, detail="[400] tables must not be empty")

    t0 = time.perf_counter()
    shape = ";".join(
        f"{t.name}:r{len(t.rows)}c{len(t.schema_)}" for t in req.tables
    )
    log.info(
        "execute_plan start tables=%d shape=%s steps=%d step_summary=%s",
        len(req.tables),
        shape,
        len(req.plan.steps),
        _step_type_summary(req.plan),
    )

    # 将请求中的表转换为执行引擎使用的 TableData 结构。
    tables: Dict[str, TableData] = {}
    for t in req.tables:
        schema_cols: list[SchemaCol] = []
        for col in t.schema_:
            key = str(col.get("key", ""))
            if not key:
                continue
            col_type = str(col.get("type", "string"))
            if col_type not in ("number", "string", "date"):
                col_type = "string"
            schema_cols.append(SchemaCol(key=key, type=col_type))  # type: ignore[arg-type]
        tables[t.name] = TableData(name=t.name, rows=list(t.rows), schema=schema_cols)

    if len(tables) == 1:
        # 单表场景：复用 apply_plan，返回与多表相同的响应结构。
        name, table = next(iter(tables.items()))
        result: ApplyResult = apply_plan(table.rows, table.schema, req.plan)
        out_table = TableData(name=name, rows=result.rows, schema=result.schema)
        execute_table = _tabledata_to_execute_table(out_table)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.info(
            "execute_plan done mode=single elapsed_ms=%.2f new_tables=0 diff_added=%d diff_modified=%d",
            elapsed_ms,
            len(result.diff.get("addedColumns", [])),
            len(result.diff.get("modifiedColumns", [])),
        )
        return ExecutePlanResponse(
            tables={name: execute_table},
            diff=result.diff,
            newTables=[],
        )

    # 多表场景：使用 apply_project_plan。
    project_result: ProjectApplyResult = apply_project_plan(tables, req.plan)
    out_tables: Dict[str, object] = {}
    for name, t in project_result.tables.items():
        out_tables[name] = _tabledata_to_execute_table(t)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "execute_plan done mode=multi elapsed_ms=%.2f new_tables=%d out_tables=%d diff_added=%d diff_modified=%d",
        elapsed_ms,
        len(project_result.new_tables),
        len(project_result.tables),
        len(project_result.diff.get("addedColumns", [])),
        len(project_result.diff.get("modifiedColumns", [])),
    )
    return ExecutePlanResponse(
        tables=out_tables,
        diff=project_result.diff,
        newTables=project_result.new_tables,
    )


def _tabledata_to_execute_table(table: TableData):
    """将内部 TableData 转换为 ExecuteTable，以便通过 Pydantic 序列化。"""
    schema_payload = [{"key": c.key, "type": c.type} for c in table.schema]
    # 延迟导入以避免循环依赖。
    from app.models import ExecuteTable

    return ExecuteTable(name=table.name, rows=list(table.rows), schema=schema_payload)


@router.post("/projects/{project_id}/plan", response_model=PlanResponse)
async def project_plan_from_store(
    project_id: str,
    req: ProjectPlanByIdRequest,
) -> PlanResponse:
    """基于后端 ProjectState 生成项目级 Plan（多表），前端只需传 prompt 与模型信息。"""
    t0 = time.perf_counter()
    state = project_store.get_project(project_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"[404] Project not found: {project_id!r}")

    # 将 ProjectState 中的表转换为 ProjectPrompt 所需的数据结构。
    tables_data: List[Dict[str, object]] = []
    for name, t in state.tables.items():
        rows = t.get("rows") or []
        schema = t.get("schema") or []
        sample_rows = rows[:10]
        tables_data.append(
            {
                "name": name,
                "schema": schema,
                "sampleRows": sample_rows,
            }
        )

    if not tables_data:
        raise HTTPException(
            status_code=400,
            detail=f"[400] Project {project_id!r} has no tables",
        )

    log.info(
        "plan_request mode=project_id project_id=%s model_source=%s tables=%d prompt_len=%d",
        project_id,
        req.modelSource or "cloud",
        len(tables_data),
        len(req.prompt or ""),
    )
    prompt = ProjectPrompt()
    user_content = prompt.build_user_content(req.prompt, tables_data)
    model_source = req.modelSource or "cloud"
    messages = prompt.single_turn_messages(user_content)
    try:
        content = await call_llm(
            model_source=model_source,
            messages=messages,
            cloud_model_id=req.cloudModelId,
            local_model_id=req.localModelId,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"[400] {e}")
    except RuntimeError as e:
        raise _http_exception_from_runtime(e)

    plan_obj = await _parse_and_validate_plan(
        content=content,
        retry_user=user_content + "\nReturn ONLY JSON.",
        system_prompt=prompt.system,
        model_source=model_source,
        cloud_model_id=req.cloudModelId,
        local_model_id=req.localModelId,
    )
    log.info(
        "plan_response mode=project_id project_id=%s steps=%d step_summary=%s elapsed_ms=%.2f",
        project_id,
        len(plan_obj.steps),
        _step_type_summary(plan_obj),
        (time.perf_counter() - t0) * 1000,
    )
    return PlanResponse(plan=plan_obj)


@router.post("/projects/{project_id}/execute-plan", response_model=ExecutePlanResponse)
async def execute_project_plan(
    project_id: str,
    req: ExecuteProjectPlanRequest,
) -> ExecutePlanResponse:
    """基于后端 ProjectState 执行 Plan，并将结果写回 ProjectStore。"""
    t0 = time.perf_counter()
    state = project_store.get_project(project_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"[404] Project not found: {project_id!r}")

    # 将 ProjectState 中的表转为执行引擎的 TableData。
    tables: Dict[str, TableData] = {}
    for name, t in state.tables.items():
        raw_schema = t.get("schema") or []
        schema_cols: List[SchemaCol] = []
        for col in raw_schema:
            key = str(col.get("key", ""))
            if not key:
                continue
            col_type = str(col.get("type", "string"))
            if col_type not in ("number", "string", "date"):
                col_type = "string"
            schema_cols.append(SchemaCol(key=key, type=col_type))  # type: ignore[arg-type]
        tables[name] = TableData(
            name=name,
            rows=list(t.get("rows") or []),
            schema=schema_cols,
        )

    if not tables:
        raise HTTPException(
            status_code=400,
            detail=f"[400] Project {project_id!r} has no tables",
        )

    log.info(
        "execute_project_plan start project_id=%s tables=%d shape=%s steps=%d step_summary=%s",
        project_id,
        len(tables),
        _tables_shape_summary(tables),
        len(req.plan.steps),
        _step_type_summary(req.plan),
    )
    result = apply_project_plan(tables, req.plan)

    # 将新的表状态写回 ProjectStore。
    persisted_tables: Dict[str, Dict[str, object]] = {}
    for name, t in result.tables.items():
        persisted_tables[name] = {
            "name": t.name,
            "rows": t.rows,
            "schema": [{"key": c.key, "type": c.type} for c in t.schema],
        }
    project_store.update_tables(project_id, persisted_tables)

    out_tables: Dict[str, object] = {}
    for name, t in result.tables.items():
        out_tables[name] = _tabledata_to_execute_table(t)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "execute_project_plan done project_id=%s elapsed_ms=%.2f new_tables=%d diff_added=%d diff_modified=%d",
        project_id,
        elapsed_ms,
        len(result.new_tables),
        len(result.diff.get("addedColumns", [])),
        len(result.diff.get("modifiedColumns", [])),
    )
    return ExecutePlanResponse(
        tables=out_tables,
        diff=result.diff,
        newTables=result.new_tables,
    )
