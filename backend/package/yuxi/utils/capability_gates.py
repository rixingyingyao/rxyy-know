"""没配公网 key 时，对普通用户隐藏对应能力。

事故背景：试用账号是对外开放的普通 user。`.env.prod` 里 `TAVILY_API_KEY` /
`SILICONFLOW_API_KEY` 为空时，网页搜索、深度研究和生图在运行时必然失败，
但智能体 / Skill 列表仍会把它们亮给访客，造成「点了就报错」的假能力。
rxyy 0903 拍板：没 key 就对普通用户隐藏这三项；管理员仍可见（方便配 key 后验收）。

Tavily 工具本身只在有 key 时注册；这里再挡一层智能体和 Skill 入口。
"""

from __future__ import annotations

import os

ADMIN_ROLES = frozenset({"admin", "superadmin"})

TAVILY_GATED_AGENT_SLUGS = frozenset(
    {
        "deep-research",
        "web-search",
        "research-explorer",
        "fact-verifier",
    }
)
TAVILY_GATED_SKILL_SLUGS = frozenset({"deep-research"})
SILICONFLOW_GATED_SKILL_SLUGS = frozenset({"image-gen"})


def env_key_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def has_tavily_key() -> bool:
    return env_key_present("TAVILY_API_KEY")


def has_siliconflow_key() -> bool:
    return env_key_present("SILICONFLOW_API_KEY")


def is_admin_role(role: str | None) -> bool:
    return role in ADMIN_ROLES


def agent_hidden_for_user(slug: str | None, role: str | None) -> bool:
    if is_admin_role(role) or not slug:
        return False
    return slug in TAVILY_GATED_AGENT_SLUGS and not has_tavily_key()


def skill_hidden_for_user(slug: str | None, role: str | None) -> bool:
    if is_admin_role(role) or not slug:
        return False
    if slug in TAVILY_GATED_SKILL_SLUGS and not has_tavily_key():
        return True
    if slug in SILICONFLOW_GATED_SKILL_SLUGS and not has_siliconflow_key():
        return True
    return False
