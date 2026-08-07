"""评估用例执行器：进程内调用 FastAPI app（复用 tests/test_cloud_llm_sample_e2e.py 的
TestClient 模式），跑通 `/api/load-sample` -> `/api/data/upload` -> `/api/agent` ->
`/api/execute-plan`，按 server/evaluation/cases.py 中的用例定义做结构/执行/行为断言。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi.testclient import TestClient

from app.main import app
from app.models.plan import Plan

from .cases import CASES, EvalCase, EvalRunContext


@dataclass
class EvalCaseResult:
    id: str
    title: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    step_count: Optional[int] = None
    error: Optional[str] = None


def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def load_sample_tables(client: TestClient) -> dict[str, dict[str, Any]]:
    """加载 test-data/sample.xlsx 的全部表（全量 rows，非截断样本）。"""
    resp = client.get("/api/load-sample")
    resp.raise_for_status()
    data = resp.json()
    return {t["name"]: t for t in data["tables"]}


def _plan_tables_payload(
    client: TestClient, all_tables: dict[str, dict[str, Any]], names: tuple[str, ...]
) -> list[dict[str, Any]]:
    """上传全量 rows 到 data store，返回携带 tableRef 的 agent 请求表列表。"""
    payload: list[dict[str, Any]] = []
    for n in names:
        t = all_tables[n]
        upload = client.post(
            "/api/data/upload",
            json={"name": n, "schema": t["schema"], "rows": t["rows"]},
        )
        upload.raise_for_status()
        payload.append(
            {"name": n, "schema": t["schema"], "tableRef": upload.json()["tableId"]}
        )
    return payload


def _execute_tables_payload(
    all_tables: dict[str, dict[str, Any]], names: tuple[str, ...]
) -> list[dict[str, Any]]:
    return [
        {"name": n, "schema": all_tables[n]["schema"], "rows": all_tables[n]["rows"]}
        for n in names
    ]


def _check_no_silent_target(data: dict[str, Any], known_tables: set[str]) -> list[str]:
    """模糊目标请求未澄清时：plan 必须存在，且每个 write step 显式指定合法 table。"""
    plan_data = data.get("plan")
    if plan_data is None:
        return [f"expected clarification or plan, got kind={data.get('kind')!r}"]
    try:
        plan = Plan.model_validate(plan_data)
    except Exception as exc:
        return [f"Plan validation failed: {exc}"]
    failures: list[str] = []
    for idx, step in enumerate(plan.steps):
        if step.action not in ("add_column", "transform_column"):
            continue
        table = getattr(step, "table", None)
        if not table:
            failures.append(f"step #{idx} {step.action}: no explicit table (silent target)")
        elif table not in known_tables:
            failures.append(f"step #{idx} {step.action}: unknown table {table!r}")
    return failures


def run_case(
    client: TestClient,
    case: EvalCase,
    all_tables: dict[str, dict[str, Any]],
    *,
    model_source: str,
    cloud_model_id: Optional[str],
    local_model_id: Optional[str],
) -> EvalCaseResult:
    t0 = time.perf_counter()
    try:
        tables_payload = _plan_tables_payload(client, all_tables, case.target_tables)
    except Exception as exc:
        return EvalCaseResult(case.id, case.title, False, elapsed_ms=_elapsed_ms(t0), error=str(exc))
    body: dict[str, Any] = {
        "prompt": case.prompt,
        "tables": tables_payload,
        "modelSource": model_source,
    }
    if cloud_model_id:
        body["cloudModelId"] = cloud_model_id
    if local_model_id:
        body["localModelId"] = local_model_id

    try:
        resp = client.post("/api/agent", json=body)
    except Exception as exc:  # network / connection errors (Ollama not running, etc.)
        return EvalCaseResult(case.id, case.title, False, elapsed_ms=_elapsed_ms(t0), error=str(exc))

    if resp.status_code != 200:
        return EvalCaseResult(
            case.id,
            case.title,
            False,
            elapsed_ms=_elapsed_ms(t0),
            error=f"HTTP {resp.status_code}: {resp.text[:300]}",
        )

    data = resp.json()

    if case.ambiguous_target:
        is_clarification = data.get("kind") == "clarification" and bool(
            (data.get("clarification") or {}).get("options")
        )
        if is_clarification:
            return EvalCaseResult(case.id, case.title, True, elapsed_ms=_elapsed_ms(t0))
        failures = _check_no_silent_target(data, set(case.target_tables))
        return EvalCaseResult(
            case.id, case.title, not failures, failures, elapsed_ms=_elapsed_ms(t0)
        )

    plan_data = data.get("plan")
    if plan_data is None:
        return EvalCaseResult(
            case.id,
            case.title,
            False,
            elapsed_ms=_elapsed_ms(t0),
            error=f"no plan in response: {str(data)[:300]}",
        )

    try:
        plan = Plan.model_validate(plan_data)
    except Exception as exc:
        return EvalCaseResult(
            case.id, case.title, False, elapsed_ms=_elapsed_ms(t0), error=f"Plan validation failed: {exc}"
        )

    failures: list[str] = []
    used_actions = {step.action for step in plan.steps}
    missing_actions = case.required_actions - used_actions
    if missing_actions:
        failures.append(f"missing required actions: {sorted(missing_actions)}")
    if len(plan.steps) < case.min_steps:
        failures.append(f"expected >= {case.min_steps} steps, got {len(plan.steps)}")

    exec_resp = client.post(
        "/api/execute-plan",
        json={
            "plan": plan_data,
            "tables": _execute_tables_payload(all_tables, case.target_tables),
        },
    )
    if exec_resp.status_code != 200:
        failures.append(f"execute-plan HTTP {exec_resp.status_code}: {exec_resp.text[:300]}")
        return EvalCaseResult(
            case.id, case.title, False, failures, elapsed_ms=_elapsed_ms(t0), step_count=len(plan.steps)
        )

    exec_data = exec_resp.json()
    if case.check is not None:
        ctx = EvalRunContext(
            plan=plan,
            result_tables=exec_data.get("tables", {}),
            new_tables=exec_data.get("newTables", []),
        )
        failures.extend(case.check(ctx))

    return EvalCaseResult(
        case.id,
        case.title,
        not failures,
        failures,
        elapsed_ms=_elapsed_ms(t0),
        step_count=len(plan.steps),
    )


def run_all(
    *,
    model_source: str = "cloud",
    cloud_model_id: Optional[str] = None,
    local_model_id: Optional[str] = None,
    case_ids: Optional[list[str]] = None,
) -> list[EvalCaseResult]:
    """Run eval cases. Defaults match ``python -m evaluation`` CLI (cloud + server model)."""
    cases = [c for c in CASES if not case_ids or c.id in case_ids]
    client = TestClient(app)
    all_tables = load_sample_tables(client)
    return [
        run_case(
            client,
            case,
            all_tables,
            model_source=model_source,
            cloud_model_id=cloud_model_id,
            local_model_id=local_model_id,
        )
        for case in cases
    ]


def print_report(results: list[EvalCaseResult]) -> int:
    """打印用例报告，返回进程退出码（有 fail/error 时非 0）。"""
    print(f"{'CASE':42} {'STATUS':7} {'STEPS':6} {'MS':>8}")
    print("-" * 68)
    for r in results:
        status = "ERROR" if r.error else ("PASS" if r.passed else "FAIL")
        steps = "-" if r.step_count is None else str(r.step_count)
        print(f"{r.id:42} {status:7} {steps:>6} {r.elapsed_ms:8.0f}")
        if r.error:
            print(f"    error: {r.error}")
        for f in r.failures:
            print(f"    - {f}")

    total = len(results)
    errored = sum(1 for r in results if r.error)
    passed = sum(1 for r in results if r.passed and not r.error)
    failed = total - passed - errored
    print("-" * 68)
    print(f"{passed}/{total} passed, {failed} failed, {errored} errored")
    return 0 if failed == 0 and errored == 0 else 1
