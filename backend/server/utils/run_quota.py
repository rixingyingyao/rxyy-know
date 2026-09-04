"""普通用户的每日对话配额。

`POST /api/agent/runs`（含外部 agent-call / eval 入口）原来对 user 角色没有任何次数、token 限制，
模型是 qwen3.7-plus、摘要阈值 900K，试用账号对外开放后被脚本刷一晚就是真钱。这里按
Asia/Shanghai 自然日给非管理员两道闸：

- 每日消息数：当天由该用户创建的 chat / resume run 条数
- 每日 token：当天该用户所有对话里 assistant 消息 `token_count` 之和
  （`chat_service` 落库时从模型返回的 `usage_metadata.total_tokens` 回填）

超限返回 429，管理员不受限；阈值从环境变量读，<=0 表示不限。
"""

from __future__ import annotations

import datetime as dt
import os

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.storage.postgres.models_business import AgentRun, Conversation, Message, User
from yuxi.utils.datetime_utils import SHANGHAI_TZ, UTC, format_utc_datetime, utc_now

from server.utils.auth_middleware import get_db, get_required_user

ADMIN_ROLES = frozenset({"admin", "superadmin"})
# subagent run 由 worker 内部派生，不算用户发的消息；它的 token 仍会通过 assistant 消息计入 token 闸。
COUNTED_RUN_TYPES = ("chat", "resume")

USER_DAILY_MESSAGE_LIMIT = int(os.getenv("USER_DAILY_MESSAGE_LIMIT", "60"))
USER_DAILY_TOKEN_LIMIT = int(os.getenv("USER_DAILY_TOKEN_LIMIT", "500000"))


def is_admin_role(role: str | None) -> bool:
    return role in ADMIN_ROLES


def quota_enabled() -> bool:
    return USER_DAILY_MESSAGE_LIMIT > 0 or USER_DAILY_TOKEN_LIMIT > 0


def quota_day_window(now: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime]:
    """当天（Asia/Shanghai）的 [起点, 次日起点)，以 naive UTC 表示，与库里 created_at 同口径。"""
    local_now = (now or utc_now()).astimezone(SHANGHAI_TZ)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + dt.timedelta(days=1)
    return (
        local_start.astimezone(UTC).replace(tzinfo=None),
        local_end.astimezone(UTC).replace(tzinfo=None),
    )


async def get_daily_usage(db: AsyncSession, uid: str, day_start: dt.datetime) -> tuple[int, int]:
    """返回 (今日消息数, 今日 token 数)。"""
    messages_used = await db.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.uid == uid,
            AgentRun.run_type.in_(COUNTED_RUN_TYPES),
            AgentRun.created_at >= day_start,
        )
    )
    tokens_used = await db.scalar(
        select(func.coalesce(func.sum(Message.token_count), 0))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.uid == uid,
            Message.role == "assistant",
            Message.created_at >= day_start,
        )
    )
    return int(messages_used or 0), int(tokens_used or 0)


def build_quota_snapshot(
    user: User,
    *,
    messages_used: int,
    tokens_used: int,
    day_end: dt.datetime,
) -> dict:
    return {
        "unlimited": is_admin_role(user.role) or not quota_enabled(),
        "messages": {"limit": USER_DAILY_MESSAGE_LIMIT, "used": messages_used},
        "tokens": {"limit": USER_DAILY_TOKEN_LIMIT, "used": tokens_used},
        "reset_at": format_utc_datetime(day_end),
    }


def _exceeded_kind(messages_used: int, tokens_used: int) -> str | None:
    if 0 < USER_DAILY_MESSAGE_LIMIT <= messages_used:
        return "messages"
    if 0 < USER_DAILY_TOKEN_LIMIT <= tokens_used:
        return "tokens"
    return None


def quota_exceeded_exception(kind: str, *, messages_used: int, tokens_used: int, day_end: dt.datetime) -> HTTPException:
    reset_local = day_end.replace(tzinfo=UTC).astimezone(SHANGHAI_TZ)
    reset_text = reset_local.strftime("%m-%d %H:%M")
    if kind == "messages":
        limit, used = USER_DAILY_MESSAGE_LIMIT, messages_used
        message = f"今日对话次数已用完（{used}/{limit}），{reset_text}（北京时间）后自动恢复"
    else:
        limit, used = USER_DAILY_TOKEN_LIMIT, tokens_used
        message = f"今日对话 token 额度已用完（{used}/{limit}），{reset_text}（北京时间）后自动恢复"
    retry_after = max(1, int((day_end - utc_now().replace(tzinfo=None)).total_seconds()))
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "quota_exceeded",
            "kind": kind,
            "message": message,
            "limit": limit,
            "used": used,
            "reset_at": format_utc_datetime(day_end),
        },
        headers={"Retry-After": str(retry_after)},
    )


async def get_quota_snapshot(db: AsyncSession, user: User) -> dict:
    day_start, day_end = quota_day_window()
    messages_used, tokens_used = await get_daily_usage(db, str(user.uid), day_start)
    return build_quota_snapshot(user, messages_used=messages_used, tokens_used=tokens_used, day_end=day_end)


async def ensure_run_quota(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """创建 run 的接口用：登录 + 非管理员未超今日配额。"""
    if is_admin_role(current_user.role) or not quota_enabled():
        return current_user

    day_start, day_end = quota_day_window()
    messages_used, tokens_used = await get_daily_usage(db, str(current_user.uid), day_start)
    kind = _exceeded_kind(messages_used, tokens_used)
    if kind:
        raise quota_exceeded_exception(kind, messages_used=messages_used, tokens_used=tokens_used, day_end=day_end)
    return current_user
