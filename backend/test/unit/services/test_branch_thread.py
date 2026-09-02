from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.conversation_service import branch_thread_view
from yuxi.storage.postgres.models_business import Base

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def test_branch_thread_copies_messages_through_cutoff_and_keeps_source(session, monkeypatch):
    monkeypatch.setattr(
        "yuxi.services.conversation_service._seed_branch_checkpoint",
        AsyncMock(),
    )
    db = session
    repo = ConversationRepository(db)
    source = await repo.create_conversation(
        uid="u1",
        agent_id="default-chatbot",
        title="模型身份询问",
        thread_id="source-thread",
        metadata={"backend_id": "ChatbotAgent"},
    )
    first = await repo.add_message(source.id, role="user", content="你是什么模型？")
    second = await repo.add_message(source.id, role="assistant", content="alibaba:qwen3.7-plus")
    await repo.add_message(source.id, role="user", content="再解释一遍")

    branched = await branch_thread_view(
        thread_id="source-thread",
        message_id=second.id,
        db=db,
        current_uid="u1",
    )

    assert branched["id"] != "source-thread"
    assert branched["title"] == "模型身份询问（分支）"
    assert branched["metadata"]["branched_from_thread_id"] == "source-thread"
    assert branched["metadata"]["branched_from_message_id"] == second.id

    source_messages = await repo.get_messages_by_thread_id("source-thread")
    branch_messages = await repo.get_messages_by_thread_id(branched["id"])
    assert [item.content for item in source_messages] == [
        "你是什么模型？",
        "alibaba:qwen3.7-plus",
        "再解释一遍",
    ]
    assert [item.content for item in branch_messages] == ["你是什么模型？", "alibaba:qwen3.7-plus"]
    assert first.id not in {item.id for item in branch_messages}


async def test_branch_thread_can_exclude_cutoff_for_edit_resend(session, monkeypatch):
    monkeypatch.setattr(
        "yuxi.services.conversation_service._seed_branch_checkpoint",
        AsyncMock(),
    )
    db = session
    repo = ConversationRepository(db)
    source = await repo.create_conversation(
        uid="u1",
        agent_id="default-chatbot",
        title="编辑重发",
        thread_id="edit-source",
        metadata={"backend_id": "ChatbotAgent"},
    )
    first = await repo.add_message(source.id, role="user", content="旧问题")
    await repo.add_message(source.id, role="assistant", content="旧回答")
    third = await repo.add_message(source.id, role="user", content="要改的问题")

    branched = await branch_thread_view(
        thread_id="edit-source",
        message_id=third.id,
        db=db,
        current_uid="u1",
        include_cutoff=False,
    )

    branch_messages = await repo.get_messages_by_thread_id(branched["id"])
    source_messages = await repo.get_messages_by_thread_id("edit-source")
    assert [item.content for item in source_messages] == ["旧问题", "旧回答", "要改的问题"]
    assert [item.content for item in branch_messages] == ["旧问题", "旧回答"]
    assert first.id not in {item.id for item in branch_messages}
    assert third.id not in {item.id for item in branch_messages}
