from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from yuxi.agents.toolkits.kbs import tools


def _tool_callable(tool):
    callback = getattr(tool, "coroutine", None)
    if callback is not None:
        return callback

    callback = getattr(tool, "func", None)
    if callback is not None:
        return callback

    raise AssertionError(f"{tool.name} tool has no callable entry")


def _query_kb_callable():
    return _tool_callable(tools.query_kb)


def _find_kb_document_callable():
    return _tool_callable(tools.find_kb_document)


def _open_kb_document_callable():
    return _tool_callable(tools.open_kb_document)


async def _run_tool(callback, **kwargs):
    result = callback(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _run_query_kb(**kwargs):
    return await _run_tool(_query_kb_callable(), **kwargs)


async def _run_find_kb_document(**kwargs):
    return await _run_tool(_find_kb_document_callable(), **kwargs)


async def _run_open_kb_document(**kwargs):
    return await _run_tool(_open_kb_document_callable(), **kwargs)


def _build_test_window(content: str, offset: int = 0, limit: int = 1800) -> dict:
    lines = content.splitlines()
    start = min(max(offset, 0), len(lines))
    selected = lines[start : start + limit]
    end = start + len(selected)
    return {
        "start_line": start + 1 if selected else 0,
        "end_line": end,
        "total_lines": len(lines),
        "offset": start,
        "window_size": limit,
        "has_more_before": start > 0,
        "has_more_after": end < len(lines),
        "next_offset": end if end < len(lines) else None,
        "content": "\n".join(f"{start + idx + 1:6d}\t{line}" for idx, line in enumerate(selected)),
    }


def _patch_retrievers(monkeypatch, *, kb_type: str = "milvus", retriever=None):
    async def _not_configured(*args, **kwargs):
        del args, kwargs
        raise AssertionError("knowledge base method is not configured for this test")

    manager = SimpleNamespace(
        get_retrievers=lambda: {
            "db-1": {
                "name": "FAQ",
                "retriever": retriever or object(),
                "metadata": {"kb_type": kb_type},
            }
        },
        find_file_content=_not_configured,
        open_file_content=_not_configured,
    )
    monkeypatch.setattr(tools, "_get_knowledge_base", lambda: manager)
    monkeypatch.setattr(tools, "knowledge_base", manager, raising=False)
    return manager


async def _fake_visible_kbs(runtime):
    del runtime
    return [{"kb_id": "db-1", "name": "FAQ"}]


@pytest.mark.asyncio
async def test_query_kb_returns_search_schema_without_sandbox_paths(monkeypatch) -> None:
    async def _fake_retriever(query_text: str, **kwargs):
        assert query_text == "auth"
        assert kwargs == {}
        return [
            {
                "content": "auth guide",
                "metadata": {
                    "file_id": "file-1",
                    "source": "auth-guide.pdf",
                    "filepath": "/tmp/sandbox/auth-guide.pdf",
                },
            }
        ]

    _patch_retrievers(monkeypatch, retriever=_fake_retriever)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_id="db-1", query_text="auth", runtime=runtime)

    assert result["kb_id"] == "db-1"
    assert result["results"][0]["id"] == "file-1:1"
    assert result["results"][0]["kb_id"] == "db-1"
    assert result["results"][0]["file_id"] == "file-1"
    assert result["results"][0]["content"] == "auth guide"
    assert result["results"][0]["metadata"]["source"] == "auth-guide.pdf"
    assert "filepath" not in result["results"][0]["metadata"]
    assert "parsed_path" not in result["results"][0]["metadata"]


@pytest.mark.asyncio
async def test_query_kb_allows_dify_knowledge_base(monkeypatch) -> None:
    async def _fake_retriever(query_text: str, **kwargs):
        assert query_text == "auth"
        return [
            {
                "content": "auth guide",
                "score": 0.98,
                "metadata": {
                    "file_id": "dify-doc-1",
                    "chunk_id": "dify-segment-1",
                    "source": "Dify Doc",
                },
            }
        ]

    _patch_retrievers(monkeypatch, kb_type="dify", retriever=_fake_retriever)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_id="db-1", query_text="auth", runtime=runtime)

    assert result == {
        "kb_id": "db-1",
        "results": [
            {
                "id": "dify-segment-1",
                "kb_id": "db-1",
                "file_id": "dify-doc-1",
                "content": "auth guide",
                "metadata": {
                    "file_id": "dify-doc-1",
                    "chunk_id": "dify-segment-1",
                    "source": "Dify Doc",
                    "score": 0.98,
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_query_kb_returns_plain_result_without_path_injection(monkeypatch) -> None:
    async def _fake_retriever(query_text: str, **kwargs):
        assert query_text == "auth"
        return "Milvus context"

    _patch_retrievers(monkeypatch, retriever=_fake_retriever)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_id="db-1", query_text="auth", runtime=runtime)

    assert result == "Milvus context"


@pytest.mark.asyncio
async def test_query_kb_maps_full_doc_id_and_chunk_metadata(monkeypatch) -> None:
    async def _fake_retriever(query_text: str, **kwargs):
        assert query_text == "auth"
        return [
            {
                "content": "auth guide",
                "full_doc_id": "file-1",
                "chunk_id": "chunk-1",
                "chunk_index": 3,
            }
        ]

    _patch_retrievers(monkeypatch, retriever=_fake_retriever)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_query_kb(kb_id="db-1", query_text="auth", runtime=runtime)

    assert result["results"][0] == {
        "id": "chunk-1",
        "kb_id": "db-1",
        "file_id": "file-1",
        "content": "auth guide",
        "metadata": {"chunk_index": 3},
    }


@pytest.mark.asyncio
async def test_find_kb_document_returns_context_windows(monkeypatch) -> None:
    _patch_retrievers(monkeypatch)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    async def _fake_find_file_content(
        kb_id: str,
        file_id: str,
        patterns: list[str],
        *,
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_windows: int = 5,
        window_size: int = 80,
    ):
        assert kb_id == "db-1"
        assert file_id == "file-1"
        assert patterns == ["token"]
        assert use_regex is False
        assert case_sensitive is False
        assert max_windows == 5
        assert window_size == 80
        return {
            "semantic": False,
            "match_mode": "keyword",
            "total_matches": 2,
            "windows": [
                {
                    "start_line": 1,
                    "end_line": 3,
                    "matched_lines": [2],
                    "content": "     1\tintro\n     2\ttoken value\n     3\toutro",
                }
            ],
        }

    monkeypatch.setattr(tools.knowledge_base, "find_file_content", _fake_find_file_content)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_find_kb_document(
        kb_id="db-1",
        file_id="file-1",
        patterns=["token"],
        runtime=runtime,
    )

    assert result == {
        "kb_id": "db-1",
        "file_id": "file-1",
        "semantic": False,
        "match_mode": "keyword",
        "total_matches": 2,
        "windows": [
            {
                "start_line": 1,
                "end_line": 3,
                "matched_lines": [2],
                "content": "     1\tintro\n     2\ttoken value\n     3\toutro",
            }
        ],
    }


@pytest.mark.asyncio
async def test_find_kb_document_rejects_dify(monkeypatch) -> None:
    _patch_retrievers(monkeypatch, kb_type="dify")
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_find_kb_document(
        kb_id="db-1",
        file_id="file-1",
        patterns=["token"],
        runtime=runtime,
    )

    assert "Dify 知识库" in result


@pytest.mark.asyncio
async def test_open_kb_document_reads_markdown_content_by_default_window(monkeypatch) -> None:
    lines = [f"line {index}" for index in range(1, 2001)]

    _patch_retrievers(monkeypatch)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    async def _fake_open_file_content(kb_id: str, file_id: str, offset: int = 0, limit: int = 1800):
        assert kb_id == "db-1"
        assert file_id == "file-1"
        return _build_test_window("\n".join(lines), offset=offset, limit=limit)

    monkeypatch.setattr(tools.knowledge_base, "open_file_content", _fake_open_file_content)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(kb_id="db-1", file_id="file-1", runtime=runtime)

    assert result["kb_id"] == "db-1"
    assert result["file_id"] == "file-1"
    assert result["start_line"] == 1
    assert result["end_line"] == 1800
    assert result["total_lines"] == 2000
    assert result["window_size"] == 1800
    assert result["has_more_before"] is False
    assert result["has_more_after"] is True
    assert result["next_offset"] == 1800
    assert "     1\tline 1" in result["content"]
    assert "  1800\tline 1800" in result["content"]


@pytest.mark.asyncio
async def test_open_kb_document_prefers_line_over_offset(monkeypatch) -> None:
    lines = [f"line {index}" for index in range(1, 1001)]

    _patch_retrievers(monkeypatch)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    async def _fake_open_file_content(kb_id: str, file_id: str, offset: int = 0, limit: int = 1800):
        assert kb_id == "db-1"
        assert file_id == "file-1"
        return _build_test_window("\n".join(lines), offset=offset, limit=limit)

    monkeypatch.setattr(tools.knowledge_base, "open_file_content", _fake_open_file_content)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(
        kb_id="db-1",
        file_id="file-1",
        line=801,
        offset=0,
        window_size=10,
        runtime=runtime,
    )

    assert result["offset"] == 800
    assert result["start_line"] == 801
    assert result["end_line"] == 810
    assert result["has_more_before"] is True
    assert result["has_more_after"] is True
    assert result["next_offset"] == 810
    assert "   801\tline 801" in result["content"]


@pytest.mark.asyncio
async def test_open_kb_document_rejects_invisible_resource(monkeypatch) -> None:
    async def _fake_visible_kbs(runtime):
        del runtime
        return [{"kb_id": "db-2", "name": "FAQ"}]

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(kb_id="db-1", file_id="file-1", runtime=runtime)

    assert "不存在或当前会话未启用" in result


@pytest.mark.asyncio
async def test_open_kb_document_requires_markdown_content(monkeypatch) -> None:
    _patch_retrievers(monkeypatch)
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    async def _fake_open_file_content(kb_id: str, file_id: str, offset: int = 0, limit: int = 1800):
        del kb_id, file_id, offset, limit
        raise Exception("文件 file-1 没有解析后的 Markdown 内容")

    monkeypatch.setattr(tools.knowledge_base, "open_file_content", _fake_open_file_content)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_open_kb_document(kb_id="db-1", file_id="file-1", runtime=runtime)

    assert "没有解析后的 Markdown 内容" in result


def _search_file_callable():
    return _tool_callable(tools.search_file)


async def _run_search_file(**kwargs):
    return await _run_tool(_search_file_callable(), **kwargs)


@pytest.mark.asyncio
async def test_search_file_requires_kb_name_or_query(monkeypatch) -> None:
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_search_file(runtime=runtime)

    assert "不能同时为空" in result


@pytest.mark.asyncio
async def test_search_file_returns_files_by_query(monkeypatch) -> None:
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    from types import SimpleNamespace as SN

    fake_files = [
        SN(
            file_id="file-1",
            filename="test.pdf",
            file_type="file",
            status="indexed",
            created_at=None,
            updated_at=None,
            file_size=1024,
        ),
        SN(
            file_id="file-2",
            filename="test2.pdf",
            file_type="file",
            status="indexed",
            created_at=None,
            updated_at=None,
            file_size=2048,
        ),
        SN(
            file_id="file-3",
            filename="other.pdf",
            file_type="file",
            status="indexed",
            created_at=None,
            updated_at=None,
            file_size=512,
        ),
    ]

    async def _fake_list_by_kb_id_after(self, kb_id, *, after_file_id=None, limit=500, files_only=False):
        return fake_files

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    monkeypatch.setattr(KnowledgeFileRepository, "list_by_kb_id_after", _fake_list_by_kb_id_after)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_search_file(query="test", runtime=runtime)

    assert result["total"] == 2
    assert len(result["files"]) == 2
    assert result["files"][0]["filename"] == "test.pdf"
    assert result["files"][1]["filename"] == "test2.pdf"


@pytest.mark.asyncio
async def test_search_file_returns_all_files_when_query_empty(monkeypatch) -> None:
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    from types import SimpleNamespace as SN

    fake_files = [
        SN(
            file_id="file-1",
            filename="test.pdf",
            file_type="file",
            status="indexed",
            created_at=None,
            updated_at=None,
            file_size=1024,
        ),
        SN(
            file_id="file-2",
            filename="other.pdf",
            file_type="file",
            status="indexed",
            created_at=None,
            updated_at=None,
            file_size=2048,
        ),
    ]

    async def _fake_list_by_kb_id_after(self, kb_id, *, after_file_id=None, limit=500, files_only=False):
        return fake_files

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    monkeypatch.setattr(KnowledgeFileRepository, "list_by_kb_id_after", _fake_list_by_kb_id_after)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_search_file(kb_name="FAQ", runtime=runtime)

    assert result["total"] == 2
    assert len(result["files"]) == 2


@pytest.mark.asyncio
async def test_search_file_pagination(monkeypatch) -> None:
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    from types import SimpleNamespace as SN

    fake_files = [
        SN(
            file_id=f"file-{i}",
            filename=f"file{i}.pdf",
            file_type="file",
            status="indexed",
            created_at=None,
            updated_at=None,
            file_size=1024 * i,
        )
        for i in range(10)
    ]

    async def _fake_list_by_kb_id_after(self, kb_id, *, after_file_id=None, limit=500, files_only=False):
        return fake_files

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    monkeypatch.setattr(KnowledgeFileRepository, "list_by_kb_id_after", _fake_list_by_kb_id_after)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_search_file(kb_name="FAQ", offset=2, limit=3, runtime=runtime)

    assert result["total"] == 10
    assert len(result["files"]) == 3
    assert result["offset"] == 2
    assert result["limit"] == 3
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_search_file_rejects_invisible_kb(monkeypatch) -> None:
    async def _fake_visible_kbs(runtime):
        del runtime
        return [{"kb_id": "db-2", "name": "Other"}]

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_search_file(kb_name="FAQ", query="test", runtime=runtime)

    assert "不存在或当前会话未启用" in result


@pytest.mark.asyncio
async def test_search_file_total_reflects_full_set_not_page(monkeypatch) -> None:
    """total/has_more 必须基于全量文件，而非按 limit/offset 截断的窗口。"""
    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible_kbs)

    from types import SimpleNamespace as SN

    fake_files = [
        SN(
            file_id=f"file-{i:02d}",
            filename=f"file{i}.pdf",
            file_type="file",
            status="indexed",
            created_at=None,
            updated_at=None,
            file_size=1024,
        )
        for i in range(50)
    ]

    async def _fake_list_by_kb_id_after(self, kb_id, *, after_file_id=None, limit=500, files_only=False):
        # 真实仓储会按 limit 截断；此 mock 同样遵守 limit，以暴露按 limit+offset 取数导致的 total 失真。
        return fake_files[:limit]

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    monkeypatch.setattr(KnowledgeFileRepository, "list_by_kb_id_after", _fake_list_by_kb_id_after)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_search_file(kb_name="FAQ", offset=0, limit=10, runtime=runtime)

    assert result["total"] == 50
    assert len(result["files"]) == 10
    assert result["has_more"] is True
