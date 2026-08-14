"""intent_analyzer：批量 LLM 分类回填 TableProfile.topic/description/granularity。

见 .cursor/plans/intent-analyzer-semantic-fields.plan.md。mock create_pa_agent，
不打真实 LLM。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from app.agent.context_assembler import build_data_context_text, is_data_profile_message
from app.agent.sub_agents import intent_analyzer as ia
from app.models.agent_models import AgentState, TableContext
from app.models.table_models import (
    ColumnProfile,
    DataContext,
    TableIntent,
    TableIntentBatch,
    TableProfile,
)

SCHEMA = [{"key": "price"}, {"key": "status"}]


def _col(name: str = "price") -> ColumnProfile:
    return ColumnProfile(
        name=name,
        inferred_type="numeric",
        count=3,
        null_count=0,
        null_ratio=0.0,
        distinct_count=3,
    )


def _profile(name: str) -> TableProfile:
    return TableProfile(
        table_name=name, total_row_count=3, col_count=1, columns=[_col()]
    )


def _state(
    dc: DataContext | None,
    *,
    table_id: str | None = "t1",
    messages: list[dict] | None = None,
) -> AgentState:
    return AgentState(
        tables=[TableContext(name="Sheet1", schema=SCHEMA, table_id=table_id)],
        messages=messages or [],
        user_prompt="sum",
        data_context=dc,
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    ia.reset_intent_cache_for_tests()
    yield
    ia.reset_intent_cache_for_tests()


class _FakeResult:
    def __init__(self, output: TableIntentBatch) -> None:
        self.output = output


class _FakeAgent:
    def __init__(
        self, output: TableIntentBatch | None = None, exc: Exception | None = None
    ) -> None:
        self._output = output
        self._exc = exc
        self.run_calls: list[str] = []

    async def run(self, prompt: str, **kwargs):
        self.run_calls.append(prompt)
        if self._exc:
            raise self._exc
        return _FakeResult(self._output)


def test_no_data_context_is_noop() -> None:
    state = _state(None)
    out = asyncio.run(ia.analyze_intent(state))
    assert out is state


def test_empty_tables_is_noop() -> None:
    state = _state(DataContext(tables=[]))
    out = asyncio.run(ia.analyze_intent(state))
    assert out.data_context is not None
    assert out.data_context.tables == []


def test_classify_fills_fields_and_refreshes_profile_message() -> None:
    dc = DataContext(tables=[_profile("Sheet1")])
    old_profile_text = build_data_context_text(dc)
    schema_msg = {"role": "user", "content": "Spreadsheet schema:\n[...]"}
    old_profile_msg = {"role": "user", "content": old_profile_text}
    state = _state(dc, messages=[old_profile_msg, schema_msg])

    fake_output = TableIntentBatch(
        tables=[
            TableIntent(
                table_name="Sheet1",
                topic="订单",
                description="每行一笔订单",
                granularity="每行=一笔订单",
            )
        ]
    )
    fake_agent = _FakeAgent(output=fake_output)
    with patch.object(ia, "create_pa_agent", return_value=fake_agent):
        out = asyncio.run(ia.analyze_intent(state))

    tp = out.data_context.tables[0]
    assert tp.topic == "订单"
    assert tp.description == "每行一笔订单"
    assert tp.granularity == "每行=一笔订单"

    # 仍只有一条 profile 消息，且原地替换为含新字段的文本
    profile_msgs = [m for m in out.messages if is_data_profile_message(m)]
    assert len(profile_msgs) == 1
    assert "topic: 订单" in profile_msgs[0]["content"]
    assert out.messages[-1] == schema_msg


def test_no_profile_message_in_transcript_is_left_alone() -> None:
    """context_analyzer 跳过注入的场景（无 schema 消息）：intent_analyzer 也不注入。"""
    dc = DataContext(tables=[_profile("Sheet1")])
    state = _state(dc, messages=[])

    fake_output = TableIntentBatch(
        tables=[TableIntent(table_name="Sheet1", topic="订单")]
    )
    fake_agent = _FakeAgent(output=fake_output)
    with patch.object(ia, "create_pa_agent", return_value=fake_agent):
        out = asyncio.run(ia.analyze_intent(state))

    assert out.data_context.tables[0].topic == "订单"
    assert out.messages == []


def test_llm_failure_fail_open_leaves_fields_none() -> None:
    dc = DataContext(tables=[_profile("Sheet1")])
    state = _state(dc)
    fake_agent = _FakeAgent(exc=RuntimeError("boom"))
    with patch.object(ia, "create_pa_agent", return_value=fake_agent):
        out = asyncio.run(ia.analyze_intent(state))
    tp = out.data_context.tables[0]
    assert tp.topic is None
    assert tp.description is None
    assert tp.granularity is None


def test_unknown_table_name_in_result_silently_dropped() -> None:
    dc = DataContext(tables=[_profile("Sheet1")])
    state = _state(dc)
    fake_output = TableIntentBatch(tables=[TableIntent(table_name="Other", topic="x")])
    fake_agent = _FakeAgent(output=fake_output)
    with patch.object(ia, "create_pa_agent", return_value=fake_agent):
        out = asyncio.run(ia.analyze_intent(state))
    assert out.data_context.tables[0].topic is None


def test_cache_hit_skips_second_llm_call() -> None:
    dc = DataContext(tables=[_profile("Sheet1")])
    state = _state(dc, table_id="tid-1")
    fake_output = TableIntentBatch(
        tables=[TableIntent(table_name="Sheet1", topic="订单")]
    )
    fake_agent = _FakeAgent(output=fake_output)
    with patch.object(ia, "create_pa_agent", return_value=fake_agent) as create_mock:
        first = asyncio.run(ia.analyze_intent(state))
        second = asyncio.run(ia.analyze_intent(state))
    assert create_mock.call_count == 1
    assert first.data_context.tables[0].topic == "订单"
    assert second.data_context.tables[0].topic == "订单"


def test_missing_table_id_skips_cache() -> None:
    dc = DataContext(tables=[_profile("Sheet1")])
    state = _state(dc, table_id=None)
    fake_output = TableIntentBatch(
        tables=[TableIntent(table_name="Sheet1", topic="x")]
    )
    fake_agent = _FakeAgent(output=fake_output)
    with patch.object(ia, "create_pa_agent", return_value=fake_agent) as create_mock:
        asyncio.run(ia.analyze_intent(state))
        asyncio.run(ia.analyze_intent(state))
    assert create_mock.call_count == 2


def test_partial_cache_hit_only_prompts_uncached_table() -> None:
    state_ab = AgentState(
        tables=[
            TableContext(name="A", schema=SCHEMA, table_id="ta"),
            TableContext(name="B", schema=SCHEMA, table_id="tb"),
        ],
        messages=[],
        user_prompt="x",
        data_context=DataContext(tables=[_profile("A"), _profile("B")]),
    )

    # 第一次：只有 A 出现在 data_context，缓存 A
    fake_output_a = TableIntentBatch(
        tables=[TableIntent(table_name="A", topic="topicA")]
    )
    only_a_state = state_ab.model_copy(
        update={"data_context": DataContext(tables=[_profile("A")])}
    )
    with patch.object(ia, "create_pa_agent", return_value=_FakeAgent(output=fake_output_a)):
        asyncio.run(ia.analyze_intent(only_a_state))

    # 第二次：A+B 都在，A 应命中缓存，只有 B 进 prompt
    fake_output_b = TableIntentBatch(
        tables=[TableIntent(table_name="B", topic="topicB")]
    )
    fake_agent_b = _FakeAgent(output=fake_output_b)
    with patch.object(ia, "create_pa_agent", return_value=fake_agent_b):
        out = asyncio.run(ia.analyze_intent(state_ab))

    assert len(fake_agent_b.run_calls) == 1
    assert "A" not in fake_agent_b.run_calls[0]
    assert "B" in fake_agent_b.run_calls[0]
    by_name = {t.table_name: t for t in out.data_context.tables}
    assert by_name["A"].topic == "topicA"
    assert by_name["B"].topic == "topicB"


def test_node_intent_graph_roundtrip_refreshes_profile_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """context_analyzer → intent_analyzer 全链路：data_context 与 transcript 均含新字段。"""
    import app.config as config_mod
    from app.agent.orchestrator import _node_context, _node_intent
    from app.services.data_store import get_data_store, reset_data_store_for_tests

    db_path = tmp_path / "intent.sqlite3"
    monkeypatch.setattr(config_mod.settings, "DATA_DB_PATH", str(db_path))
    reset_data_store_for_tests()
    try:
        store = get_data_store()
        tid = store.create_table(
            "Sheet1",
            SCHEMA,
            [{"price": 1, "status": "a"}, {"price": 2, "status": "b"}],
        )
        state = AgentState(
            tables=[TableContext(name="Sheet1", schema=SCHEMA, table_id=tid)],
            messages=[{"role": "user", "content": "Spreadsheet schema:\n[...]"}],
            user_prompt="sum",
        )
        ctx_out = _node_context({"agent": state.model_dump(), "scratch": {}})

        fake_output = TableIntentBatch(
            tables=[TableIntent(table_name="Sheet1", topic="订单")]
        )
        fake_agent = _FakeAgent(output=fake_output)
        with patch.object(ia, "create_pa_agent", return_value=fake_agent):
            intent_out = asyncio.run(_node_intent(ctx_out))

        restored = AgentState.model_validate(intent_out["agent"])
        assert restored.data_context is not None
        assert restored.data_context.tables[0].topic == "订单"
        profile_msg = next(
            m for m in restored.messages if is_data_profile_message(m)
        )
        assert "topic: 订单" in profile_msg["content"]
    finally:
        reset_data_store_for_tests()


def test_classify_wall_clock_is_bounded_by_per_request_timeout() -> None:
    """intent 的总耗时被 ``(SDK_MAX_RETRIES + 1) × INTENT_TIMEOUT_S`` 封顶。

    ``analyze_intent`` 没有外层 ``asyncio.wait_for``，也**不需要**：per-request 上限
    （``ModelSettings(timeout=INTENT_TIMEOUT_S)``，2026-07-23 #44 引入）配上钉死的
    SDK 传输层重试次数，wall-clock 已经有界。本用例把这个「两个常量共同封顶」的关系
    锁住——任何一个被改回默认值，上界都会变，这里会红。

    按 1/100 缩放跑，只验关系不验绝对值。
    """
    import time

    import httpx

    from app.services import llm as llm_http
    from app.services import llm_pydantic_ai as pa_llm

    per_request = 0.05
    attempts = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        await asyncio.sleep(per_request)
        raise httpx.ReadTimeout("simulated hang", request=request)

    async def run() -> float:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        llm_http.set_shared_llm_http_client(client)
        t0 = time.perf_counter()
        try:
            await ia.classify_table_intents(
                [_profile("Sheet1")], {"Sheet1": "t1"}, "cloud",
                cloud_model_id="test/model",
            )
        except Exception:  # noqa: BLE001 — fail-open 在更上层，这里只测耗时
            pass
        finally:
            elapsed = time.perf_counter() - t0
            llm_http.set_shared_llm_http_client(None)
            await client.aclose()
        return elapsed

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test")
        mp.setattr(ia, "INTENT_TIMEOUT_S", per_request)
        elapsed = asyncio.run(run())

    max_attempts = pa_llm.SDK_MAX_RETRIES + 1
    assert attempts["n"] <= max_attempts
    # 留出退避睡眠的余量：只验「被 per-request × 尝试次数 封顶」这个数量级关系。
    assert elapsed < per_request * max_attempts + 2.0
