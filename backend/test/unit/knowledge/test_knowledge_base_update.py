import asyncio
import types

from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.knowledge.base import KnowledgeBase


class FakeKnowledgeBase(KnowledgeBase):
    @property
    def kb_type(self) -> str:
        return "fake"

    async def _create_kb_instance(self, slug: str, config: dict):
        return None

    async def _initialize_kb_instance(self, instance) -> None:
        pass

    async def index_file(self, slug: str, file_id: str, operator_id: str | None = None) -> dict:
        return {}

    async def update_content(self, slug: str, file_ids: list[str], params: dict | None = None) -> list[dict]:
        return []

    async def aquery(self, query_text: str, slug: str, **kwargs) -> list[dict]:
        return []

    def get_query_params_config(self, slug: str, **kwargs) -> dict:
        return {"options": []}

    async def delete_file(self, slug: str, file_id: str) -> None:
        pass

    async def get_file_basic_info(self, slug: str, file_id: str) -> dict:
        return {}

    async def get_file_content(self, slug: str, file_id: str) -> dict:
        return {}

    async def get_file_info(self, slug: str, file_id: str) -> dict:
        return {}

    async def _save_metadata(self) -> None:
        pass


def make_kb(tmp_path):
    kb = FakeKnowledgeBase(str(tmp_path))
    kb.databases_meta = {
        "db": {
            "name": "Old name",
            "description": "Old description",
            "kb_type": "fake",
            "llm_model_spec": "provider:model-a",
        }
    }
    return kb


def make_file_record(file_id: str, meta: dict):
    return types.SimpleNamespace(
        file_id=file_id,
        kb_id=meta.get("kb_id"),
        parent_id=meta.get("parent_id"),
        filename=meta.get("filename", ""),
        file_type=meta.get("file_type"),
        path=meta.get("path"),
        minio_url=meta.get("minio_url"),
        markdown_file=meta.get("markdown_file"),
        status=meta.get("status"),
        content_hash=meta.get("content_hash"),
        file_size=meta.get("size", meta.get("file_size")),
        chunk_count=meta.get("chunk_count", 0),
        token_count=meta.get("token_count", 0),
        content_type=meta.get("content_type"),
        processing_params=meta.get("processing_params"),
        is_folder=meta.get("is_folder", False),
        error_message=meta.get("error"),
        created_by=meta.get("created_by"),
        updated_by=meta.get("updated_by"),
        created_at=None,
        updated_at=None,
        original_filename=meta.get("original_filename"),
    )


class FakeFileRepository:
    def __init__(self, records: dict[str, types.SimpleNamespace]):
        self.records = records
        self.update_calls = []

    async def list_by_kb_id(self, kb_id: str):
        return [record for record in self.records.values() if record.kb_id == kb_id]

    async def list_by_kb_id_after(
        self,
        kb_id: str,
        *,
        after_file_id: str | None = None,
        limit: int = 500,
        files_only: bool = False,
    ):
        records = [
            record
            for record in self.records.values()
            if record.kb_id == kb_id
            and (not after_file_id or record.file_id > after_file_id)
            and (not files_only or not record.is_folder)
        ]
        records.sort(key=lambda record: record.file_id)
        return records[:limit]

    async def update_fields(self, *, file_id: str, data: dict, kb_id: str | None = None):
        record = self.records.get(file_id)
        if record is None or (kb_id and record.kb_id != kb_id):
            return None
        for key, value in data.items():
            setattr(record, key, value)
        self.update_calls.append((file_id, kb_id, dict(data)))
        return record

    async def get_kb_file_stats(self, kb_id: str):
        records = [record for record in self.records.values() if record.kb_id == kb_id]
        files = [record for record in records if not record.is_folder]
        return {
            "row_count": len(records),
            "file_count": len(files),
            "folder_count": len(records) - len(files),
            "total_size": sum(int(record.file_size or 0) for record in files),
            "chunk_count": sum(int(record.chunk_count or 0) for record in files),
            "token_count": sum(int(record.token_count or 0) for record in files),
            "pending_parse_count": sum(1 for record in files if record.status == "uploaded"),
            "pending_index_count": sum(1 for record in files if record.status in {"parsed", "error_indexing"}),
            "processing_count": sum(1 for record in files if record.status in {"processing", "waiting", "parsing", "indexing"}),
        }


def make_file_records(files: dict[str, dict]) -> dict[str, types.SimpleNamespace]:
    return {file_id: make_file_record(file_id, meta) for file_id, meta in files.items()}


async def test_create_database_persists_allowed_record_fields(tmp_path, monkeypatch):
    created_payloads = []

    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            return None

        async def create(self, payload):
            created_payloads.append(payload)
            return types.SimpleNamespace(**payload)

        async def update(self, kb_id, data):
            raise AssertionError("create_database should insert new database metadata")

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        FakeKnowledgeBaseRepository,
    )

    kb = FakeKnowledgeBase(str(tmp_path))
    share_config = {"access_level": "user", "department_ids": [], "user_uids": ["root"]}

    await kb.create_database(
        "New database",
        "New description",
        embedding_model_spec="provider:embedding",
        record_fields={
            "share_config": share_config,
            "created_by": "root",
            "unexpected_field": "ignored",
        },
        auto_generate_questions=False,
    )

    assert len(created_payloads) == 1
    payload = created_payloads[0]
    assert payload["share_config"] == share_config
    assert payload["created_by"] == "root"
    assert "unexpected_field" not in payload
    assert "share_config" not in payload["additional_params"]
    assert "created_by" not in payload["additional_params"]


async def test_update_database_keeps_llm_spec_when_field_is_omitted(tmp_path):
    kb = make_kb(tmp_path)

    result = kb.update_database("db", "New name", "New description")
    await asyncio.sleep(0)

    assert result["llm_model_spec"] == "provider:model-a"
    assert kb.databases_meta["db"]["llm_model_spec"] == "provider:model-a"


async def test_update_database_clears_llm_spec_when_field_is_explicit(tmp_path):
    kb = make_kb(tmp_path)

    result = kb.update_database("db", "New name", "New description", None, update_llm_model_spec=True)
    await asyncio.sleep(0)

    assert result["llm_model_spec"] is None
    assert kb.databases_meta["db"]["llm_model_spec"] is None


def test_get_database_info_returns_persisted_content_stats(tmp_path):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {
        "stats": {"row_count": 3, "file_count": 2, "chunk_count": 5, "token_count": 25}
    }

    result = kb.get_database_info("db")

    assert result["row_count"] == 3
    assert result["stats"]["file_count"] == 2
    assert result["stats"]["chunk_count"] == 5
    assert result["stats"]["token_count"] == 25
    assert result["files"] == {}
    assert result["files_truncated"] is True


def test_get_database_info_prefers_metadata_stats(tmp_path):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {"stats": {"file_count": 2, "chunk_count": 8, "token_count": 40}}

    result = kb.get_database_info("db")

    assert result["stats"]["file_count"] == 2
    assert result["stats"]["chunk_count"] == 8
    assert result["stats"]["token_count"] == 40


async def test_refresh_database_stats_persists_metadata(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {}
    records = make_file_records({
        "file-1": {"kb_id": "db", "filename": "alpha.md", "chunk_count": 2, "token_count": 10},
        "folder-1": {
            "kb_id": "db",
            "filename": "folder",
            "is_folder": True,
            "chunk_count": 99,
            "token_count": 99,
        },
    })
    file_repo = FakeFileRepository(records)
    persisted_kbs = []

    async def persist_kb(kb_id):
        persisted_kbs.append((kb_id, dict(kb.databases_meta[kb_id]["metadata"])))

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda: file_repo,
    )
    kb._persist_kb = persist_kb

    stats = await kb.refresh_database_stats("db")

    assert stats["file_count"] == 1
    assert stats["chunk_count"] == 2
    assert stats["token_count"] == 10
    assert kb.databases_meta["db"]["metadata"]["stats"] == stats
    assert persisted_kbs == [("db", {"stats": stats})]


async def test_repair_missing_file_stats_updates_files_and_database_metadata(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {}
    records = make_file_records({
        "file-1": {"kb_id": "db", "filename": "alpha.md", "chunk_count": 0, "token_count": 0},
        "file-2": {"kb_id": "db", "filename": "beta.md", "chunk_count": 1, "token_count": 7},
        "folder-1": {
            "kb_id": "db",
            "filename": "folder",
            "is_folder": True,
            "chunk_count": 99,
            "token_count": 99,
        },
    })
    file_repo = FakeFileRepository(records)
    persisted_kbs = []

    class FakeChunkRepo:
        async def count_by_file_ids(self, file_ids):
            assert file_ids == ["file-1", "file-2"]
            return {"file-1": 2, "file-2": 3}

        async def list_by_file_ids(self, file_ids):
            assert file_ids == ["file-1"]
            return [
                types.SimpleNamespace(file_id="file-1", content="alpha beta"),
                types.SimpleNamespace(file_id="file-1", content="中文"),
            ]

    async def persist_kb(kb_id):
        persisted_kbs.append((kb_id, dict(kb.databases_meta[kb_id]["metadata"])))

    monkeypatch.setattr("yuxi.repositories.knowledge_chunk_repository.KnowledgeChunkRepository", FakeChunkRepo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda: file_repo,
    )
    kb._persist_kb = persist_kb

    result = await kb.repair_missing_file_stats("db")

    expected_token_count = count_tokens("alpha beta") + count_tokens("中文")
    expected_stats = {"file_count": 2, "chunk_count": 5, "token_count": expected_token_count + 7}
    assert records["file-1"].chunk_count == 2
    assert records["file-1"].token_count == expected_token_count
    assert records["file-2"].chunk_count == 3
    assert records["file-2"].token_count == 7
    for key, value in expected_stats.items():
        assert result["stats"][key] == value
    assert result["scanned_token_files"] == 1
    assert result["updated_chunk_files"] == 2
    assert result["updated_token_files"] == 1
    assert {file_id for file_id, _, _ in file_repo.update_calls} == {"file-1", "file-2"}
    persisted_stats = persisted_kbs[0][1]["stats"]
    for key, value in expected_stats.items():
        assert persisted_stats[key] == value


async def test_repair_missing_file_stats_skips_unindexed_files(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {}
    records = make_file_records({
        "file-indexed": {
            "kb_id": "db",
            "filename": "alpha.md",
            "status": "indexed",
            "chunk_count": 0,
            "token_count": 0,
        },
        "file-uploaded": {
            "kb_id": "db",
            "filename": "beta.md",
            "status": "uploaded",
            "chunk_count": 9,
            "token_count": 90,
        },
        "file-parsed": {
            "kb_id": "db",
            "filename": "gamma.md",
            "status": "parsed",
            "chunk_count": 3,
            "token_count": 30,
        },
    })
    file_repo = FakeFileRepository(records)

    class FakeChunkRepo:
        async def count_by_file_ids(self, file_ids):
            assert file_ids == ["file-indexed"]
            return {"file-indexed": 2}

        async def list_by_file_ids(self, file_ids):
            assert file_ids == ["file-indexed"]
            return [types.SimpleNamespace(file_id="file-indexed", content="alpha beta")]

    async def persist_kb(kb_id):
        pass

    monkeypatch.setattr("yuxi.repositories.knowledge_chunk_repository.KnowledgeChunkRepository", FakeChunkRepo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda: file_repo,
    )
    kb._persist_kb = persist_kb

    result = await kb.repair_missing_file_stats("db")

    expected_token_count = count_tokens("alpha beta")
    assert records["file-indexed"].chunk_count == 2
    assert records["file-indexed"].token_count == expected_token_count
    assert records["file-uploaded"].chunk_count == 0
    assert records["file-uploaded"].token_count == 0
    assert records["file-parsed"].chunk_count == 0
    assert records["file-parsed"].token_count == 0
    assert result["stats"]["file_count"] == 3
    assert result["stats"]["chunk_count"] == 2
    assert result["stats"]["token_count"] == expected_token_count
    assert result["scanned_files"] == 3
    assert result["scanned_indexed_files"] == 1
    assert result["skipped_unindexed_files"] == 2
    assert result["updated_files"] == 3
    assert {file_id for file_id, _, _ in file_repo.update_calls} == {
        "file-indexed",
        "file-uploaded",
        "file-parsed",
    }
