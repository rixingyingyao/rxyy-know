"""没 key 时对普通用户隐藏深度研究 / 网页检索 / 生图（0903 rxyy 拍板）。

事故背景：TAVILY / SILICONFLOW 为空时工具注册失败，但全局共享的 deep-research /
image-gen / web-search 仍出现在试用账号的智能体和 Skill 列表里，点进去必炸。
这里锁住：普通用户没 key 看不见、管理员仍可见、有 key 后普通用户恢复可见。
"""

from __future__ import annotations

from yuxi.agents.skills.service import user_can_access_skill
from yuxi.repositories.agent_repository import user_can_access_agent
from yuxi.storage.postgres.models_business import Agent, Skill, User
from yuxi.utils import capability_gates as gates


def _user(uid: str, role: str = "user") -> User:
    return User(username=uid, uid=uid, password_hash="x", role=role, department_id=1)


def _agent(slug: str) -> Agent:
    return Agent(
        slug=slug,
        name=slug,
        backend_id="ChatbotAgent",
        created_by="admin",
        share_config={"access_level": "global", "department_ids": [], "user_uids": []},
    )


def _skill(slug: str) -> Skill:
    return Skill(
        slug=slug,
        name=slug,
        description="",
        created_by="builtin-system",
        enabled=True,
        source_type="builtin",
        share_config={"access_level": "global", "department_ids": [], "user_uids": []},
    )


def test_regular_user_cannot_see_tavily_agents_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    user = _user("trial")
    for slug in ("deep-research", "web-search", "research-explorer", "fact-verifier"):
        assert user_can_access_agent(user, _agent(slug)) is False
    assert user_can_access_agent(user, _agent("default-chatbot")) is True


def test_regular_user_sees_tavily_agents_when_key_present(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-free")
    user = _user("trial")
    assert user_can_access_agent(user, _agent("deep-research")) is True
    assert user_can_access_agent(user, _agent("web-search")) is True


def test_admin_still_sees_gated_agents_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    for role in ("admin", "superadmin"):
        assert user_can_access_agent(_user("boss", role=role), _agent("deep-research")) is True


def test_regular_user_cannot_see_image_gen_without_siliconflow(monkeypatch):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    user = _user("trial")
    assert user_can_access_skill(user, _skill("image-gen")) is False
    assert user_can_access_skill(user, _skill("deep-research")) is False
    assert user_can_access_skill(user, _skill("knowledge-base")) is True


def test_regular_user_sees_image_gen_when_siliconflow_present(monkeypatch):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    assert user_can_access_skill(_user("trial"), _skill("image-gen")) is True


def test_admin_still_sees_gated_skills_without_keys(monkeypatch):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert user_can_access_skill(_user("boss", role="admin"), _skill("image-gen")) is True
    assert user_can_access_skill(_user("boss", role="admin"), _skill("deep-research")) is True


def test_blank_env_values_count_as_missing(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "   ")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "")
    assert gates.has_tavily_key() is False
    assert gates.has_siliconflow_key() is False
    assert gates.agent_hidden_for_user("deep-research", "user") is True
    assert gates.skill_hidden_for_user("image-gen", "user") is True
