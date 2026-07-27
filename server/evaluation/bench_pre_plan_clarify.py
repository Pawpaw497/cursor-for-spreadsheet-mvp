"""隔离测量脚本：直接调用 ``select_relevant_tables`` / ``scan_for_ambiguity``，
测真实（未受 0.8s 常量限制的）延迟分布，供阈值校准使用。

见 .cursor/plans/pre-plan-clarify-scan-timeout-tuning.plan.md（measure-real-latency-distribution）。

要点（设计已定稿，勿改动）：
- 本阶段**不**包 ``asyncio.wait_for(..., PRE_PLAN_TIMEOUT_S/CLARIFY_SCAN_TIMEOUT_S)``——
  只受 ``ModelSettings(timeout=OPENROUTER_HTTP_TIMEOUT_CHAT_S)`` 约束，测的是裸 LLM
  往返延迟本身，不是现有 0.8s 超时墙。有 ``asyncio.wait_for`` 封顶、测「完成率」的验证
  是另一阶段（``verify-fast-path-with-bench-script``，见 ``--wrap-timeout``），两者
  目的不同，不要混用。
- ``select_relevant_tables`` 的 prompt 经 ``render_table_index_for_pre_plan`` 渲染，
  含 topic/description（先跑一次 ``intent_analyzer`` 回填）；``scan_for_ambiguity`` 的
  prompt 只有 ``user_prompt`` + 表名列表，不需要 intent 回填。
- 每个 (model, prompt, module) 组合串行跑，避免并发触发 OpenRouter 限流扭曲 p99；
  ``--rounds`` > 1 时每轮独立丢弃 warmup 后拼接样本，用于降低单轮方差影响。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi.testclient import TestClient

from app.agent.sub_agents import clarify_scan, pre_plan
from app.agent.sub_agents.intent_analyzer import analyze_intent
from app.agent.sub_agents.profile_builder import build_table_profile
from app.main import app
from app.models.agent_models import AgentState, TableContext
from app.models.table_models import DataContext

from .cases import CASES
from .runner import load_sample_tables

DEFAULT_MODELS = [
    "google/gemini-2.5-flash-lite",
    "openai/gpt-4o-mini",
    "deepseek/deepseek-v4-pro",
]
DEFAULT_CASE_IDS = [
    "category_summary_multitable",
    "ambiguous_add_column_no_silent_target",
    "ambiguous_join_key_semantic_clarify",
]
# 三个目标 case 恰好共用同一对表，intent 回填按模型跑一次即可复用。
BENCH_TABLE_NAMES = ("销售订单", "产品信息")


@dataclass
class RunStats:
    model_id: str
    prompt_id: str
    module: Literal["pre_plan", "clarify_scan"]
    samples_ms: list[float] = field(default_factory=list)
    errors: int = 0
    timeouts: int = 0

    def summary(self) -> dict[str, Any]:
        s = sorted(self.samples_ms)
        n = len(s)

        def pct(p: float) -> float:
            if not s:
                return float("nan")
            k = min(n - 1, int(round(p * (n - 1))))
            return s[k]

        return {
            "model_id": self.model_id,
            "prompt_id": self.prompt_id,
            "module": self.module,
            "n": n,
            "p50_ms": round(pct(0.50), 1),
            "p90_ms": round(pct(0.90), 1),
            "p99_ms": round(pct(0.99), 1),
            "errors": self.errors,
            "timeouts": self.timeouts,
        }


def _build_data_context(all_tables: dict[str, Any], names: tuple[str, ...]) -> DataContext:
    profiles = [
        build_table_profile(n, all_tables[n]["schema"], all_tables[n]["rows"])
        for n in names
    ]
    return DataContext(tables=profiles)


async def _with_topics(dc: DataContext, prompt_for_intent: str, model_id: str) -> DataContext:
    """跑一次 ``intent_analyzer``，回填 topic/description（仅 pre_plan bench 需要）。"""
    state = AgentState(
        tables=[
            TableContext(name=t.table_name, schema=[{"key": c.name} for c in t.columns])
            for t in dc.tables
        ],
        messages=[],
        data_context=dc,
        user_prompt=prompt_for_intent,
        model_source="cloud",
        cloud_model_id=model_id,
    )
    enriched = await analyze_intent(state)
    assert enriched.data_context is not None
    return enriched.data_context


async def _time_calls(fn, stats: RunStats, *, runs: int, warmup: int) -> None:
    """跑 ``runs`` 次，丢弃前 ``warmup`` 次；单次调用失败（如已知的 OpenRouter
    ``finish_reason='error'`` flakiness）只计入 ``errors``/``timeouts``，不中断整个
    bench——一次 flaky 响应不该丢掉同一 (model, case, module) 组合已经跑出的样本。
    """
    for i in range(runs):
        t0 = time.perf_counter()
        try:
            await fn()
        except asyncio.TimeoutError:
            if i >= warmup:
                stats.timeouts += 1
            continue
        except Exception as exc:
            if i >= warmup:
                stats.errors += 1
            print(f"  ! call failed ({stats.model_id}/{stats.prompt_id}/{stats.module}): {exc}")
            continue
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if i >= warmup:
            stats.samples_ms.append(elapsed_ms)


async def bench(
    models: list[str],
    case_ids: list[str],
    *,
    runs: int,
    warmup: int,
    rounds: int,
    wrap_timeout: bool,
) -> list[dict[str, Any]]:
    """@param wrap_timeout: True 时复现生产路径（asyncio.wait_for 封顶新阈值），
    用于 verify-fast-path-with-bench-script 阶段测完成率；本阶段（阶段一）应为 False。
    """
    client = TestClient(app)
    all_tables = load_sample_tables(client)
    cases = [c for c in CASES if c.id in case_ids]

    results: list[RunStats] = []
    for model_id in models:
        base_dc = _build_data_context(all_tables, BENCH_TABLE_NAMES)
        dc_with_topics = await _with_topics(base_dc, cases[0].prompt, model_id)

        for case in cases:
            table_names = list(case.target_tables)

            pp_stats = RunStats(model_id, case.id, "pre_plan")
            cs_stats = RunStats(model_id, case.id, "clarify_scan")

            async def _pp_call(case=case) -> None:
                coro = pre_plan.select_relevant_tables(
                    dc_with_topics, case.prompt, "cloud", cloud_model_id=model_id
                )
                if wrap_timeout:
                    await asyncio.wait_for(coro, timeout=pre_plan.PRE_PLAN_TIMEOUT_S)
                else:
                    await coro

            async def _cs_call(case=case, table_names=table_names) -> None:
                coro = clarify_scan.scan_for_ambiguity(
                    case.prompt, table_names, "cloud", cloud_model_id=model_id
                )
                if wrap_timeout:
                    await asyncio.wait_for(coro, timeout=clarify_scan.CLARIFY_SCAN_TIMEOUT_S)
                else:
                    await coro

            for round_idx in range(rounds):
                print(f"[{model_id}/{case.id}] pre_plan round {round_idx + 1}/{rounds}...")
                await _time_calls(_pp_call, pp_stats, runs=runs, warmup=warmup)
                print(f"[{model_id}/{case.id}] clarify_scan round {round_idx + 1}/{rounds}...")
                await _time_calls(_cs_call, cs_stats, runs=runs, warmup=warmup)

            results.append(pp_stats)
            results.append(cs_stats)
            print(f"  done: {pp_stats.summary()}")
            print(f"  done: {cs_stats.summary()}")

    return [r.summary() for r in results]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m evaluation.bench_pre_plan_clarify",
        description="Bench select_relevant_tables/scan_for_ambiguity raw latency (or wrapped completion rate).",
    )
    p.add_argument("--model", dest="models", action="append", default=None)
    p.add_argument("--case", dest="case_ids", action="append", default=None)
    p.add_argument("--runs", type=int, default=25, help="Timed runs per (model, case, module) per round.")
    p.add_argument("--warmup", type=int, default=3, help="Warm-up runs discarded per round.")
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument(
        "--wrap-timeout",
        action="store_true",
        help="Wrap calls in asyncio.wait_for using the current PRE_PLAN_TIMEOUT_S/"
        "CLARIFY_SCAN_TIMEOUT_S (production-shaped completion-rate check, stage 2). "
        "Omit for stage 1 (raw latency distribution).",
    )
    p.add_argument("--json-out", default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    models = args.models or DEFAULT_MODELS
    case_ids = args.case_ids or DEFAULT_CASE_IDS
    results = asyncio.run(
        bench(
            models,
            case_ids,
            runs=args.runs,
            warmup=args.warmup,
            rounds=args.rounds,
            wrap_timeout=args.wrap_timeout,
        )
    )
    print(
        f"{'MODEL':32} {'CASE':42} {'MODULE':14} {'N':>4} {'P50':>8} {'P90':>8} {'P99':>8} "
        f"{'ERR':>4} {'TO':>4}"
    )
    print("-" * 130)
    for r in results:
        print(
            f"{r['model_id']:32} {r['prompt_id']:42} {r['module']:14} "
            f"{r['n']:4d} {r['p50_ms']:8.0f} {r['p90_ms']:8.0f} {r['p99_ms']:8.0f} "
            f"{r['errors']:4d} {r['timeouts']:4d}"
        )
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\nWrote JSON report to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
