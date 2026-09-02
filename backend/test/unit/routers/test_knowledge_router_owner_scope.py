"""普通用户可管自己的知识库之后，逐 kb 归属校验必须挡住越权（试用账号上线前的回归）。

事故背景：`knowledge_router` 原来全部 `get_admin_user`，只看角色不看 kb 归属；放开给普通用户后，
如果不按 kb_id 逐个校验，任何登录用户拿到别人的 kb_id 就能直接读写。
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from server.routers import knowledge_router
from server.utils import kb_permission

pytestmark = pytest.mark.asyncio

PRIVATE_TO_U1 = {"access_level": "user", "department_ids": [], "user_uids": ["u1"]}
GLOBAL = {"access_level": "global", "department_ids": [], "user_uids": []}
DEPT_7 = {"access_level": "department", "department_ids": [7], "user_uids": []}


def _user(uid: str, role: str = "user", department_id: int | None = 1):
    return SimpleNamespace(id=hash(uid) % 1000, uid=uid, role=role, department_id=department_id)


def _install_fake_repo(monkeypatch, kbs: dict[str, SimpleNamespace], owned_counts: dict[str, int] | None = None):
    class FakeRepo:
        async def get_by_kb_id(self, kb_id: str):
            return kbs.get(kb_id)

        async def count_created_by(self, uid: str) -> int:
            return (owned_counts or {}).get(str(uid), 0)

    monkeypatch.setattr(kb_permission, "KnowledgeBaseRepository", FakeRepo)


def _kb(created_by: str, share_config: dict):
    return SimpleNamespace(kb_id="kb_1", created_by=created_by, share_config=share_config)


async def test_user_cannot_manage_other_users_private_kb(monkeypatch):
    _install_fake_repo(monkeypatch, {"kb_1": _kb("u1", PRIVATE_TO_U1)})

    with pytest.raises(HTTPException) as exc_info:
        await kb_permission.ensure_kb_manageable("kb_1", _user("u2"))
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        await kb_permission.ensure_kb_readable("kb_1", _user("u2"))
    assert exc_info.value.status_code == 403


async def test_owner_can_read_and_manage_own_kb(monkeypatch):
    _install_fake_repo(monkeypatch, {"kb_1": _kb("u1", PRIVATE_TO_U1)})

    assert (await kb_permission.ensure_kb_readable("kb_1", _user("u1"))).created_by == "u1"
    assert (await kb_permission.ensure_kb_manageable("kb_1", _user("u1"))).created_by == "u1"


async def test_user_can_read_but_not_manage_globally_shared_kb(monkeypatch):
    _install_fake_repo(monkeypatch, {"kb_1": _kb("admin", GLOBAL)})

    await kb_permission.ensure_kb_readable("kb_1", _user("u2"))
    with pytest.raises(HTTPException) as exc_info:
        await kb_permission.ensure_kb_manageable("kb_1", _user("u2"))
    assert exc_info.value.status_code == 403


async def test_admin_manages_only_accessible_kbs_and_superadmin_manages_all(monkeypatch):
    _install_fake_repo(monkeypatch, {"kb_1": _kb("u1", DEPT_7)})

    await kb_permission.ensure_kb_manageable("kb_1", _user("a7", role="admin", department_id=7))
    with pytest.raises(HTTPException) as exc_info:
        await kb_permission.ensure_kb_manageable("kb_1", _user("a9", role="admin", department_id=9))
    assert exc_info.value.status_code == 403
    await kb_permission.ensure_kb_manageable("kb_1", _user("root", role="superadmin", department_id=None))


async def test_unknown_kb_is_404_for_everyone(monkeypatch):
    _install_fake_repo(monkeypatch, {})

    for user in (_user("u1"), _user("root", role="superadmin")):
        with pytest.raises(HTTPException) as exc_info:
            await kb_permission.ensure_kb_readable("missing", user)
        assert exc_info.value.status_code == 404


async def test_kb_quota_only_limits_non_admin(monkeypatch):
    _install_fake_repo(monkeypatch, {}, owned_counts={"u1": 3, "a1": 30})
    monkeypatch.setattr(kb_permission, "KB_MAX_PER_USER", 3)

    with pytest.raises(HTTPException) as exc_info:
        await kb_permission.ensure_kb_quota(_user("u1"))
    assert exc_info.value.status_code == 403
    assert "3" in exc_info.value.detail

    await kb_permission.ensure_kb_quota(_user("a1", role="admin"))

    monkeypatch.setattr(kb_permission, "KB_MAX_PER_USER", 0)
    await kb_permission.ensure_kb_quota(_user("u1"))


async def test_force_private_share_config_pins_non_admin_to_self():
    assert kb_permission.force_private_share_config(_user("u1"), GLOBAL) == {
        "access_level": "user",
        "department_ids": [],
        "user_uids": ["u1"],
    }
    assert kb_permission.force_private_share_config(_user("a1", role="admin"), GLOBAL) == GLOBAL
    assert kb_permission.force_private_share_config(_user("a1", role="admin"), None) is None


def _build_app(current_user):
    async def fake_required_user():
        return current_user

    app = FastAPI()
    app.include_router(knowledge_router.knowledge, prefix="/api")
    app.dependency_overrides[kb_permission.get_required_user] = fake_required_user
    return app


async def test_route_get_info_can_manage_follows_created_by(monkeypatch):
    _install_fake_repo(monkeypatch, {"kb_1": _kb("u1", PRIVATE_TO_U1)})

    async def fake_get_database_info(kb_id, include_files=False):
        return {"kb_id": kb_id, "name": "mine", "created_by": "u1", "share_config": PRIVATE_TO_U1}

    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)

    app = _build_app(_user("u1"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        own = await client.get("/api/knowledge/databases/kb_1")

    other_app = _build_app(_user("u2"))
    async with AsyncClient(transport=ASGITransport(app=other_app), base_url="http://test") as other:
        foreign = await other.get("/api/knowledge/databases/kb_1")

    assert own.status_code == 200, own.text
    assert own.json()["can_manage"] is True
    assert foreign.status_code == 403, foreign.text


async def test_route_blocks_non_owner_update_and_delete(monkeypatch):
    _install_fake_repo(monkeypatch, {"kb_1": _kb("u1", PRIVATE_TO_U1)})

    async def fail_update(*_args, **_kwargs):
        raise AssertionError("越权请求不应到达 update_database")

    monkeypatch.setattr(knowledge_router.knowledge_base, "update_database", fail_update)
    monkeypatch.setattr(knowledge_router.knowledge_base, "delete_database", fail_update)

    app = _build_app(_user("u2"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        update = await client.put("/api/knowledge/databases/kb_1", json={"name": "x", "description": "y"})
        delete = await client.delete("/api/knowledge/databases/kb_1")
        upload = await client.post(
            "/api/knowledge/files/upload?kb_id=kb_1", files={"file": ("demo.txt", b"demo", "text/plain")}
        )

    assert update.status_code == 403, update.text
    assert delete.status_code == 403, delete.text
    assert upload.status_code == 403, upload.text


async def test_route_lists_can_manage_per_kb_for_regular_user(monkeypatch):
    async def fake_get_databases_by_user(_user):
        return {
            "databases": [
                {"kb_id": "mine", "created_by": "u1", "share_config": PRIVATE_TO_U1},
                {"kb_id": "shared", "created_by": "admin", "share_config": GLOBAL},
            ]
        }

    monkeypatch.setattr(knowledge_router.knowledge_base, "get_databases_by_user", fake_get_databases_by_user)

    app = _build_app(_user("u1"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/knowledge/databases")

    assert response.status_code == 200, response.text
    flags = {item["kb_id"]: item["can_manage"] for item in response.json()["databases"]}
    assert flags == {"mine": True, "shared": False}


async def test_route_create_forces_private_share_and_quota_for_regular_user(monkeypatch):
    _install_fake_repo(monkeypatch, {}, owned_counts={"u1": 0})
    monkeypatch.setattr(kb_permission, "KB_MAX_PER_USER", 1)
    captured = {}

    async def fake_name_exists(_name):
        return False

    async def fake_create_database(name, description, **kwargs):
        captured["share_config"] = kwargs.get("share_config")
        captured["created_by"] = kwargs.get("created_by")
        return {"kb_id": "new", "name": name, "created_by": kwargs.get("created_by"), "share_config": kwargs.get("share_config")}

    async def fake_reload_all():
        return None

    from yuxi.agents.buildin import agent_manager

    monkeypatch.setattr(knowledge_router.knowledge_base, "database_name_exists", fake_name_exists)
    monkeypatch.setattr(knowledge_router.knowledge_base, "create_database", fake_create_database)
    monkeypatch.setattr(agent_manager, "reload_all", fake_reload_all)
    monkeypatch.setattr(
        knowledge_router.model_cache,
        "get_model_info",
        lambda _spec: SimpleNamespace(model_type="embedding"),
    )

    payload = {
        "database_name": "试用库",
        "description": "d",
        "kb_type": "milvus",
        "embedding_model_spec": "fake/embedding",
        "share_config": GLOBAL,
    }

    app = _build_app(_user("u1"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/knowledge/databases", json=payload)
        assert created.status_code == 200, created.text
        assert created.json()["can_manage"] is True
        assert captured["share_config"] == {"access_level": "user", "department_ids": [], "user_uids": ["u1"]}
        assert captured["created_by"] == "u1"

        _install_fake_repo(monkeypatch, {}, owned_counts={"u1": 1})
        blocked = await client.post("/api/knowledge/databases", json=payload)

    assert blocked.status_code == 403, blocked.text
    assert "最多可创建 1 个知识库" in blocked.json()["detail"]
