"""知识库逐 kb 的访问校验。

`get_admin_user` 只看角色，不看 kb 归属：普通用户可以建库之后，`/knowledge/databases/{kb_id}/...`
必须按 kb_id 逐个校验，否则拿到别人的 kb_id 就能直接读写。

- reader：superadmin 直通；否则 kb 必须存在且对当前用户可访问（创建者 / global / 部门 / 指定人）
- manager：superadmin 直通；创建者直通；admin 需可访问；普通用户不是创建者 → 403
"""

from __future__ import annotations

import os

from fastapi import Depends, HTTPException, status
from yuxi import knowledge_base
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_knowledge import KnowledgeBase

from server.utils.auth_middleware import get_required_user

ADMIN_ROLES = frozenset({"admin", "superadmin"})

# 普通用户（非管理员）可创建的知识库数量上限；每个库都要占 Milvus collection 和 embedding 调用，
# 试用账号对外开放后不设上限就是无限花钱。<=0 表示不限制。
KB_MAX_PER_USER = int(os.getenv("KB_MAX_PER_USER", "3"))


def is_admin_role(role: str | None) -> bool:
    return role in ADMIN_ROLES


def user_info(user: User) -> dict:
    return {"uid": user.uid, "role": user.role, "department_id": user.department_id}


async def load_kb_or_404(kb_id: str) -> KnowledgeBase:
    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"知识库 {kb_id} 不存在")
    return kb


def kb_readable(user: User, kb: KnowledgeBase) -> bool:
    return knowledge_base.is_database_readable(user_info(user), created_by=kb.created_by, share_config=kb.share_config)


def kb_manageable(user: User, kb: KnowledgeBase) -> bool:
    return knowledge_base.is_database_manageable(
        user_info(user), created_by=kb.created_by, share_config=kb.share_config
    )


async def ensure_kb_readable(kb_id: str, user: User) -> KnowledgeBase:
    kb = await load_kb_or_404(kb_id)
    if not kb_readable(user, kb):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该知识库")
    return kb


async def ensure_kb_manageable(kb_id: str, user: User) -> KnowledgeBase:
    kb = await load_kb_or_404(kb_id)
    if not kb_manageable(user, kb):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只能管理自己创建的知识库")
    return kb


async def get_kb_reader(kb_id: str, current_user: User = Depends(get_required_user)) -> User:
    """路径带 kb_id 的只读接口用：登录 + 该 kb 对当前用户可访问。"""
    await ensure_kb_readable(kb_id, current_user)
    return current_user


async def get_kb_manager(kb_id: str, current_user: User = Depends(get_required_user)) -> User:
    """路径带 kb_id 的写接口用：登录 + 有权增删改该 kb。"""
    await ensure_kb_manageable(kb_id, current_user)
    return current_user


def force_private_share_config(user: User, share_config: dict | None) -> dict | None:
    """非管理员建/改库时把共享范围钉死为「仅自己」。

    普通用户若能把库设成 global，等于把内容推给全站所有人（并进所有人的智能体候选）；
    智能体侧 `normalize_agent_share_config(force_private=True)` 是同一口径。
    """
    if is_admin_role(user.role):
        return share_config
    return {"access_level": "user", "department_ids": [], "user_uids": [str(user.uid)]}


async def ensure_kb_quota(user: User) -> None:
    if is_admin_role(user.role) or KB_MAX_PER_USER <= 0:
        return
    owned = await KnowledgeBaseRepository().count_created_by(str(user.uid))
    if owned >= KB_MAX_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"普通用户最多可创建 {KB_MAX_PER_USER} 个知识库，请先删除不用的知识库",
        )
