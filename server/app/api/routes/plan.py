"""Plan 相关路由。"""
import json
from typing import Dict, List

from fastapi import APIRouter, HTTPException

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
    except json.JSONDecodeError:
        retry_messages = [Message.system(system_prompt), Message.user(retry_user)]
        content = await call_llm(
            model_source=model_source,
            messages=retry_messages,
            cloud_model_id=cloud_model_id,
            local_model_id=local_model_id,
        )
        json_text = extract_json(content)
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=500,
                detail=f"[500] Model did not return valid JSON: {e}. Raw: {content}",
            )
    try:
        return Plan.model_validate(parsed)
    except Exception as e:
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
        or '"code":401' in msg
    )
    if is_auth_error:
        detail = "[502] 云端 LLM 鉴权失败：请检查 OPENROUTER_API_KEY 是否正确配置，" \
                 "并在 OpenRouter 控制台确认该 Key 有效且未过期。" \
                 f"（技术详情：{msg}）"
        return HTTPException(status_code=502, detail=detail)

    return HTTPException(status_code=502, detail=f"[502] {msg}")


@router.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest):
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
    return PlanResponse(plan=plan_obj)


@router.post("/plan-project", response_model=PlanResponse)
async def plan_project(req: ProjectPlanRequest):
    """Project-level planning: accepts multiple tables and can generate join/create_table steps."""
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
    return PlanResponse(plan=plan_obj)


@router.post("/execute-plan", response_model=ExecutePlanResponse)
async def execute_plan(req: ExecutePlanRequest) -> ExecutePlanResponse:
    """无状态执行 Plan：前端携带当前 tables 与 plan，后端返回执行结果。"""
    if not req.tables:
        raise HTTPException(status_code=400, detail="[400] tables must not be empty")

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
    return PlanResponse(plan=plan_obj)


@router.post("/projects/{project_id}/execute-plan", response_model=ExecutePlanResponse)
async def execute_project_plan(
    project_id: str,
    req: ExecuteProjectPlanRequest,
) -> ExecutePlanResponse:
    """基于后端 ProjectState 执行 Plan，并将结果写回 ProjectStore。"""
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

    return ExecutePlanResponse(
        tables=out_tables,
        diff=result.diff,
        newTables=result.new_tables,
    )
