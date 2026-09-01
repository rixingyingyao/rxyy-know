from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from server.utils.auth_middleware import get_db, get_required_user
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.mention_search_service import search_mention_files_in_index
from yuxi.storage.postgres.models_business import User

mention_router = APIRouter(prefix="/mention", tags=["mention"])


class MentionFileItem(BaseModel):
    """提及文件搜索结果条目"""

    name: str
    path: str
    is_dir: bool
    source: str


@mention_router.get("/search", response_model=list[MentionFileItem])
async def search_mention_files(
    thread_id: str | None = Query(None, description="当前聊天会话 ID；为空时仅搜索用户工作区"),
    query: str = Query("", description="模糊搜索关键字"),
    sources: str | None = Query(None, description="搜索来源：workspace,thread；为空时自动选择"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """
    提及文件模糊搜索接口：未创建 thread 时只搜索用户 workspace；已有 thread 时可搜索当前对话文件。
    """
    uid = str(current_user.uid)
    effective_thread_id: str | None = None

    if thread_id:
        conv_repo = ConversationRepository(db)
        conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
        if conversation:
            if conversation.uid != uid or conversation.status == "deleted":
                raise HTTPException(status_code=404, detail="对话线程不存在")
            effective_thread_id = thread_id
        else:
            try:
                from yuxi.agents.backends.sandbox.paths import validate_thread_id

                validate_thread_id(thread_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="非法的 thread_id 格式")

    source_list = [item.strip() for item in sources.split(",")] if sources else None
    return await search_mention_files_in_index(
        thread_id=effective_thread_id,
        uid=uid,
        query=query,
        sources=source_list,
    )
