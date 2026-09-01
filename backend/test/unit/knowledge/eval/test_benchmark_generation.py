import asyncio
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.knowledge.eval import benchmark_generation
from yuxi.knowledge.eval.benchmark_generation import (
    build_benchmark_generation_prompt,
    clamp_neighbors_count,
    collect_kb_chunks,
    iter_generated_benchmark_items,
    normalize_generation_concurrency_count,
    select_graph_enhanced_chunks,
    select_neighbor_chunks_by_kb_query,
)


class FakeKnowledgeBase:
    pass


class FakeGenerationKnowledgeBase:
    def __init__(self, query_results=None):
        self.query_results = query_results or []
        self.query_calls = []

    async def aquery(self, query_text, kb_id, **kwargs):
        self.query_calls.append({"query_text": query_text, "kb_id": kb_id, **kwargs})
        return self.query_results


class FakeLlm:
    def __init__(self, gold_chunk_id="anchor_chunk"):
        self.gold_chunk_id = gold_chunk_id
        self.prompts = []

    async def call(self, prompt, stream):
        self.prompts.append(prompt)
        return SimpleNamespace(
            content=('{"query":"问题","gold_answer":"答案","gold_chunk_ids":["' + self.gold_chunk_id + '"]}')
        )


class NoQueryKnowledgeBase(FakeGenerationKnowledgeBase):
    async def aquery(self, query_text, kb_id, **kwargs):
        raise AssertionError("neighbors_count=1 时不应调用 aquery")


class TrackingLlm:
    def __init__(self, content=None, delay=0):
        self.content = content or '{"query":"问题","gold_answer":"答案","gold_chunk_ids":["anchor_chunk"]}'
        self.delay = delay
        self.active_calls = 0
        self.max_active_calls = 0
        self.calls = 0

    async def call(self, prompt, stream):
        self.calls += 1
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return SimpleNamespace(content=self.content)
        finally:
            self.active_calls -= 1


class FakeGraphGenerationKnowledgeBase(FakeGenerationKnowledgeBase):
    pass


def make_chunk(
    chunk_id: str,
    *,
    kb_id: str = "db_1",
    file_id: str = "file_a",
    content: str = "anchor content",
    chunk_index: int = 0,
    graph_indexed: bool = False,
    ent_ids: list[str] | None = None,
):
    return SimpleNamespace(
        chunk_id=chunk_id,
        kb_id=kb_id,
        file_id=file_id,
        content=content,
        chunk_index=chunk_index,
        graph_indexed=graph_indexed,
        ent_ids=ent_ids,
        tags=None,
        extraction_result=None,
    )


@pytest.fixture(autouse=True)
def fake_chunk_repository(monkeypatch):
    class FakeChunkRepository:
        chunks = [make_chunk("anchor_chunk")]

        async def list_by_kb_id(self, kb_id):
            return [chunk for chunk in self.chunks if chunk.kb_id == kb_id]

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_chunk_repository.KnowledgeChunkRepository",
        FakeChunkRepository,
    )
    return FakeChunkRepository


def test_clamp_neighbors_count():
    assert clamp_neighbors_count(-1) == 0
    assert clamp_neighbors_count(3) == 3
    assert clamp_neighbors_count(11) == 10


def test_normalize_generation_concurrency_count():
    assert normalize_generation_concurrency_count(None) == 10
    assert normalize_generation_concurrency_count("") == 10
    assert normalize_generation_concurrency_count(0) == 1
    assert normalize_generation_concurrency_count(-5) == 1
    assert normalize_generation_concurrency_count(10000) == 20


def test_build_benchmark_generation_prompt_contains_required_schema():
    prompt = build_benchmark_generation_prompt([("chunk_1", "片段内容")])

    assert "片段ID=chunk_1" in prompt
    assert "query、gold_answer、gold_chunk_ids" in prompt


@pytest.mark.asyncio
async def test_collect_kb_chunks_filters_kb_id(fake_chunk_repository):
    fake_chunk_repository.chunks = [
        make_chunk("file_a_chunk", content="内容"),
        make_chunk("file_b_chunk", kb_id="db_2", file_id="file_b", content="其他"),
    ]

    chunks = await collect_kb_chunks(FakeKnowledgeBase(), "db_1")

    assert chunks == [
        {
            "id": "file_a_chunk",
            "content": "内容",
            "file_id": "file_a",
            "chunk_index": 0,
            "graph_indexed": False,
            "ent_ids": [],
            "tags": [],
            "extraction_result": None,
        }
    ]


@pytest.mark.asyncio
async def test_iter_generated_benchmark_items_with_one_chunk_does_not_query(monkeypatch):
    fake_llm = FakeLlm()
    monkeypatch.setattr(benchmark_generation, "select_model", lambda model_spec: fake_llm)

    items = [
        item
        async for item in iter_generated_benchmark_items(
            kb_instance=NoQueryKnowledgeBase(),
            kb_id="db_1",
            count=1,
            neighbors_count=1,
            llm_model_spec="test-provider:test-model",
        )
    ]

    assert items == [{"query": "问题", "gold_chunk_ids": ["anchor_chunk"], "gold_answer": "答案"}]
    assert "片段ID=anchor_chunk" in fake_llm.prompts[0]


@pytest.mark.asyncio
async def test_select_neighbor_chunks_by_kb_query_filters_anchor():
    kb = FakeGenerationKnowledgeBase(
        query_results=[
            {
                "content": "anchor content",
                "metadata": {"chunk_id": "anchor_chunk", "file_id": "file_a", "chunk_index": 0},
            },
            {
                "content": "neighbor content",
                "metadata": {"chunk_id": "neighbor_chunk", "file_id": "file_a", "chunk_index": 1},
            },
        ]
    )

    chunks = await select_neighbor_chunks_by_kb_query(
        kb_instance=kb,
        kb_id="db_1",
        anchor_chunk={"id": "anchor_chunk", "content": "anchor content", "file_id": "file_a", "chunk_index": 0},
        neighbors_count=1,
    )

    assert chunks == [{"id": "neighbor_chunk", "content": "neighbor content", "file_id": "file_a", "chunk_index": 1}]
    assert kb.query_calls == [
        {
            "query_text": "anchor content",
            "kb_id": "db_1",
            "search_mode": "vector",
            "final_top_k": 4,
            "use_reranker": False,
            "similarity_threshold": 0.0,
        }
    ]


@pytest.mark.asyncio
async def test_select_graph_enhanced_chunks_expands_by_ppr_with_anchor_bias(monkeypatch):
    calls = []

    async def fake_rank(self, kb_id, seed_weights, *, max_nodes, top_k, damping):
        calls.append(dict(seed_weights))
        if len(calls) == 1:
            return [("anchor", 0.9), ("neighbor_1", 0.8)]
        return [("anchor", 0.9), ("neighbor_1", 0.8), ("neighbor_2", 0.7)]

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService.query_and_rank_chunks_by_ppr",
        fake_rank,
    )
    chunks_by_id = {
        "anchor": {"id": "anchor", "content": "anchor", "ent_ids": ["anchor_entity"]},
        "neighbor_1": {"id": "neighbor_1", "content": "neighbor 1", "ent_ids": ["entity_1"]},
        "neighbor_2": {"id": "neighbor_2", "content": "neighbor 2", "ent_ids": ["entity_2"]},
    }

    chunks = await select_graph_enhanced_chunks(
        kb_id="db_1",
        anchor_chunk=chunks_by_id["anchor"],
        chunks_by_id=chunks_by_id,
        context_count=3,
        graph_expand_top_k=1,
    )

    assert [chunk["id"] for chunk in chunks] == ["anchor", "neighbor_1", "neighbor_2"]
    assert calls[0] == {"anchor_entity": 1.0}
    assert calls[1]["anchor_entity"] == 1.0
    assert calls[1]["entity_1"] == 0.9


@pytest.mark.asyncio
async def test_iter_generated_benchmark_items_graph_mode_uses_graph_indexed_anchor(monkeypatch, fake_chunk_repository):
    fake_chunk_repository.chunks = [
        make_chunk(
            "vector_anchor",
            content="vector content",
            chunk_index=0,
            graph_indexed=False,
            ent_ids=["vector_entity"],
        ),
        make_chunk(
            "graph_anchor",
            content="graph anchor content",
            chunk_index=1,
            graph_indexed=True,
            ent_ids=["anchor_entity"],
        ),
        make_chunk(
            "graph_neighbor",
            content="graph neighbor content",
            chunk_index=2,
            graph_indexed=False,
            ent_ids=["neighbor_entity"],
        ),
    ]

    async def fake_rank(self, kb_id, seed_weights, *, max_nodes, top_k, damping):
        assert seed_weights["anchor_entity"] == 1.0
        return [("graph_anchor", 0.9), ("graph_neighbor", 0.8)]

    fake_llm = FakeLlm(gold_chunk_id="graph_neighbor")
    monkeypatch.setattr(benchmark_generation, "select_model", lambda model_spec: fake_llm)
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService.query_and_rank_chunks_by_ppr",
        fake_rank,
    )
    kb = FakeGraphGenerationKnowledgeBase()

    items = [
        item
        async for item in iter_generated_benchmark_items(
            kb_instance=kb,
            kb_id="db_1",
            count=1,
            neighbors_count=2,
            llm_model_spec="test-provider:test-model",
            generation_mode="graph_enhanced",
        )
    ]

    assert items == [{"query": "问题", "gold_chunk_ids": ["graph_neighbor"], "gold_answer": "答案"}]
    assert kb.query_calls == []
    assert "片段ID=graph_anchor" in fake_llm.prompts[0]
    assert "片段ID=graph_neighbor" in fake_llm.prompts[0]
    assert "片段ID=vector_anchor" not in fake_llm.prompts[0]


@pytest.mark.asyncio
async def test_iter_generated_benchmark_items_uses_query_neighbor(monkeypatch):
    fake_llm = FakeLlm(gold_chunk_id="neighbor_chunk")
    monkeypatch.setattr(benchmark_generation, "select_model", lambda model_spec: fake_llm)
    kb = FakeGenerationKnowledgeBase(
        query_results=[
            {
                "content": "neighbor content",
                "metadata": {"chunk_id": "neighbor_chunk", "file_id": "file_a", "chunk_index": 1},
            }
        ]
    )

    items = [
        item
        async for item in iter_generated_benchmark_items(
            kb_instance=kb,
            kb_id="db_1",
            count=1,
            neighbors_count=2,
            llm_model_spec="test-provider:test-model",
        )
    ]

    assert items == [{"query": "问题", "gold_chunk_ids": ["neighbor_chunk"], "gold_answer": "答案"}]
    assert kb.query_calls[0]["query_text"] == "anchor content"
    assert kb.query_calls[0]["search_mode"] == "vector"
    assert "片段ID=neighbor_chunk" in fake_llm.prompts[0]


@pytest.mark.asyncio
async def test_iter_generated_benchmark_items_falls_back_to_anchor_when_query_empty(monkeypatch):
    fake_llm = FakeLlm()
    monkeypatch.setattr(benchmark_generation, "select_model", lambda model_spec: fake_llm)

    items = [
        item
        async for item in iter_generated_benchmark_items(
            kb_instance=FakeGenerationKnowledgeBase(query_results=[]),
            kb_id="db_1",
            count=1,
            neighbors_count=2,
            llm_model_spec="test-provider:test-model",
        )
    ]

    assert items == [{"query": "问题", "gold_chunk_ids": ["anchor_chunk"], "gold_answer": "答案"}]
    assert "片段ID=anchor_chunk" in fake_llm.prompts[0]


@pytest.mark.asyncio
async def test_iter_generated_benchmark_items_respects_concurrency_count(monkeypatch):
    fake_llm = TrackingLlm(delay=0.01)
    monkeypatch.setattr(benchmark_generation, "select_model", lambda model_spec: fake_llm)

    items = [
        item
        async for item in iter_generated_benchmark_items(
            kb_instance=NoQueryKnowledgeBase(),
            kb_id="db_1",
            count=4,
            neighbors_count=1,
            concurrency_count=2,
            llm_model_spec="test-provider:test-model",
        )
    ]

    assert len(items) == 4
    assert fake_llm.max_active_calls == 2


@pytest.mark.asyncio
async def test_iter_generated_benchmark_items_returns_at_most_count(monkeypatch):
    fake_llm = TrackingLlm(delay=0.01)
    monkeypatch.setattr(benchmark_generation, "select_model", lambda model_spec: fake_llm)

    items = [
        item
        async for item in iter_generated_benchmark_items(
            kb_instance=NoQueryKnowledgeBase(),
            kb_id="db_1",
            count=3,
            neighbors_count=1,
            concurrency_count=10,
            llm_model_spec="test-provider:test-model",
        )
    ]

    assert len(items) == 3


@pytest.mark.asyncio
async def test_iter_generated_benchmark_items_stops_at_max_attempts(monkeypatch):
    fake_llm = TrackingLlm(content='{"query":"","gold_answer":"答案","gold_chunk_ids":["anchor_chunk"]}')
    monkeypatch.setattr(benchmark_generation, "select_model", lambda model_spec: fake_llm)

    items = [
        item
        async for item in iter_generated_benchmark_items(
            kb_instance=NoQueryKnowledgeBase(),
            kb_id="db_1",
            count=2,
            neighbors_count=1,
            concurrency_count=10,
            llm_model_spec="test-provider:test-model",
        )
    ]

    assert items == []
    assert fake_llm.calls == 50
