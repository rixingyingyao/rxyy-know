"""试用账号每晚清空。
trial 是所有访客共用的普通账号：访客 A 建的库、对话、智能体，访客 B 登进来全能看到还能删。
这里把该账号名下的内容整体清掉——对话（消息 / run / LangGraph checkpoint / 沙盒线程目录）、
知识库、自建智能体、API Key、个人工作区目录、Memory——让每天都是干净账号。
- 目标账号从 ``TRIAL_CLEANUP_USERNAMES`` 读（逗号分隔，默认 ``trial``）；只清 role=user 的账号，
  管理员账号即使写进去也会被跳过，避免配置手滑把自己清了
- worker 按 ``TRIAL_CLEANUP_HOUR:TRIAL_CLEANUP_MINUTE``（Asia/Shanghai）每天跑一次；
  超管也可以 ``POST /api/system/trial-cleanup`` 立刻跑一次拿到清理明细
"""
from __future__ import annotations
import os
import shutil
from pathlib import Path
from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi import config as app_config
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import (
    Agent,
    AgentRun,
    APIKey,
    Conversation,
    ConversationStats,
    Message,
    MessageFeedback,
    SubagentThread,
    ToolCall,
    User,
    UserConfig,
)
from yuxi.storage.postgres.models_knowledge import KnowledgeBase
from yuxi.utils.datetime_utils import utc_isoformat
from yuxi.utils.logging_config import logger
TRIAL_CLEANUP_USERNAMES = tuple(
    name.strip() for name in os.getenv("TRIAL_CLEANUP_USERNAMES", "trial").split(",") if name.strip()
)
TRIAL_CLEANUP_HOUR = int(os.getenv("TRIAL_CLEANUP_HOUR", "4"))
TRIAL_CLEANUP_MINUTE = int(os.getenv("TRIAL_CLEANUP_MINUTE", "30"))
# LangGraph AsyncPostgresSaver 的三张表都以 thread_id 为键；不存在（sqlite 后端）就跳过。
_CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
def cleanup_enabled() -> bool:
    return bool(TRIAL_CLEANUP_USERNAMES)
def _threads_root() -> Path:
    return Path(app_config.save_dir) / "threads"
def _remove_dir(path: Path) -> bool:
    if not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True
async def _delete_knowledge_bases(uid: str) -> list[str]:
    """走知识库管理器的正式删除，把 Milvus collection、文件、图谱一起收掉。"""
    from yuxi import knowledge_base
    async with pg_manager.get_async_session_context() as session:
        result = await session.execute(select(KnowledgeBase.kb_id).where(KnowledgeBase.created_by == uid))
        kb_ids = [row[0] for row in result.all()]
    deleted: list[str] = []
    for kb_id in kb_ids:
        try:
            await knowledge_base.delete_database(kb_id)
            deleted.append(kb_id)
        except Exception as exc:
            logger.error(f"trial cleanup: delete knowledge base {kb_id} failed: {exc}")
    if deleted:
        try:
            from yuxi.agents.buildin import agent_manager
            await agent_manager.reload_all()
        except Exception as exc:
            logger.warning(f"trial cleanup: reload agents after kb deletion failed: {exc}")
    return deleted
async def _delete_agents(db: AsyncSession, uid: str) -> list[str]:
    result = await db.execute(select(Agent).where(Agent.created_by == uid))
    agents = list(result.scalars().all())
    slugs: list[str] = []
    for agent in agents:
        if agent.is_default:
            continue
        slugs.append(agent.slug)
        await db.delete(agent)
    await db.commit()
    return slugs
async def _delete_checkpoints(db: AsyncSession, thread_ids: list[str]) -> None:
    if not thread_ids:
        return
    for table in _CHECKPOINT_TABLES:
        stmt = text(f"DELETE FROM {table} WHERE thread_id IN :thread_ids").bindparams(
            bindparam("thread_ids", expanding=True)
        )
        try:
            await db.execute(stmt, {"thread_ids": thread_ids})
        except Exception as exc:
            await db.rollback()
            logger.warning(f"trial cleanup: skip checkpoint table {table}: {exc}")
async def _delete_conversations(db: AsyncSession, uid: str) -> dict:
    """硬删该用户全部对话。
    外键顺序：tool_calls / feedbacks → messages → stats → runs → subagent_threads → conversations。"""
    rows = (await db.execute(select(Conversation.id, Conversation.thread_id).where(Conversation.uid == uid))).all()
    conversation_ids = [row[0] for row in rows]
    thread_ids = [row[1] for row in rows]
    if conversation_ids:
        message_ids = select(Message.id).where(Message.conversation_id.in_(conversation_ids))
        await db.execute(delete(ToolCall).where(ToolCall.message_id.in_(message_ids)))
        await db.execute(delete(MessageFeedback).where(MessageFeedback.message_id.in_(message_ids)))
        await db.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
        await db.execute(delete(ConversationStats).where(ConversationStats.conversation_id.in_(conversation_ids)))
    runs_result = await db.execute(delete(AgentRun).where(AgentRun.uid == uid))
    await db.execute(delete(SubagentThread).where(SubagentThread.uid == uid))
    await db.execute(delete(Conversation).where(Conversation.uid == uid))
    await db.commit()
    await _delete_checkpoints(db, thread_ids)
    await db.commit()
    removed_dirs = 0
    threads_root = _threads_root()
    for thread_id in thread_ids:
        if _remove_dir(threads_root / thread_id):
            removed_dirs += 1
    return {
        "conversations": len(conversation_ids),
        "runs": int(runs_result.rowcount or 0),
        "thread_dirs_removed": removed_dirs,
    }
async def _delete_api_keys(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(delete(APIKey).where(APIKey.user_id == user_id))
    await db.commit()
    return int(result.rowcount or 0)
async def _reset_memory(db: AsyncSession, uid: str) -> bool:
    result = await db.execute(delete(UserConfig).where(UserConfig.uid == uid))
    await db.commit()
    return bool(result.rowcount)
async def cleanup_user_data(db: AsyncSession, user: User) -> dict:
    """清掉一个普通用户名下的全部内容并返回明细。调用方负责确认 user.role == 'user'。"""
    uid = str(user.uid)
    summary: dict = {"username": user.username, "uid": uid}
    summary["knowledge_bases"] = await _delete_knowledge_bases(uid)
    summary["agents"] = await _delete_agents(db, uid)
    summary.update(await _delete_conversations(db, uid))
    summary["api_keys"] = await _delete_api_keys(db, user.id)
    summary["memory_reset"] = await _reset_memory(db, uid)
    summary["workspace_removed"] = _remove_dir(_threads_root() / "shared" / uid)
    logger.info(f"trial cleanup done for {user.username}: {summary}")
    return summary
async def cleanup_trial_users(usernames: tuple[str, ...] | list[str] | None = None) -> dict:
    targets = tuple(usernames) if usernames is not None else TRIAL_CLEANUP_USERNAMES
    results: list[dict] = []
    for username in targets:
        async with pg_manager.get_async_session_context() as db:
            result = await db.execute(select(User).where(User.username == username, User.is_deleted == 0))
            user = result.scalar_one_or_none()
            if user is None:
                results.append({"username": username, "skipped": "not_found"})
                continue
            if user.role != "user":
                logger.warning(f"trial cleanup: skip {username}, role={user.role} is not a regular user")
                results.append({"username": username, "skipped": "not_regular_user"})
                continue
            results.append(await cleanup_user_data(db, user))
    return {"cleaned_at": utc_isoformat(), "results": results}
async def run_trial_cleanup_job(ctx) -> dict:
    """ARQ cron 入口。"""
    del ctx
    return await cleanup_trial_users()
