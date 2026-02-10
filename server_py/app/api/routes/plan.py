"""Plan 相关路由。"""
import json

from fastapi import APIRouter, HTTPException

from app.models import Plan, PlanRequest, PlanResponse, ProjectPlanRequest
from app.services.llm import call_llm
from app.services.prompts import (
    Message,
    ProjectPrompt,
    SpreadsheetPrompt,
    extract_json,
)

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
                detail=f"Model did not return valid JSON: {e}. Raw: {content}",
            )
    try:
        return Plan.model_validate(parsed)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Plan validation failed: {e}. Raw: {parsed}",
        )


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
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

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
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    plan_obj = await _parse_and_validate_plan(
        content=content,
        retry_user=user_content + "\nReturn ONLY JSON.",
        system_prompt=prompt.system,
        model_source=model_source,
        cloud_model_id=req.cloudModelId,
        local_model_id=req.localModelId,
    )
    return PlanResponse(plan=plan_obj)
