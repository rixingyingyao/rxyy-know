"""试用账号加固：API Key 签发收成管理员权限（0903 rxyy 拍板）。

事故背景：`POST /api/user/apikey/` 原来只要登录就能给自己发 key，普通用户拿着 key 就能脱离网页
无限调 `/api/agent/runs`；CLI 登录批准（`/api/auth/cli/sessions/{code}/approve`）同样会给批准人
签发 key。这里锁住三条签发路径对 user 角色都是 403，管理员照常可用；已有 key 的启停 / 删除仍归持有人。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.utils.auth_middleware import get_current_user, get_db
from yuxi.utils.datetime_utils import utc_now_naive

# server.routers 包把同名 APIRouter 对象导出成了 auth_router / user_router，这里要拿的是模块本身。
auth_router = importlib.import_module("server.routers.auth_router")
user_router = importlib.import_module("server.routers.user_router")

pytestmark = pytest.mark.asyncio


def _user(uid: str, role: str = "user"):
    return SimpleNamespace(id=22 if role == "user" else 1, uid=uid, role=role, department_id=1, is_deleted=0)


class _FakeSession:
    """只满足 create_api_key 的 add / commit / refresh 三步，refresh 时补齐 to_dict 需要的字段。"""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None

    async def refresh(self, obj):
        obj.id = 1
        obj.created_at = utc_now_naive()
        obj.is_enabled = True

    async def execute(self, *args, **kwargs):
        raise AssertionError("unexpected query")

    async def delete(self, obj):
        return None


def _build_app(current_user, session=None) -> FastAPI:
    app = FastAPI()
    app.include_router(user_router.user_router, prefix="/api")
    app.include_router(auth_router.auth, prefix="/api")

    async def fake_db():
        yield session or _FakeSession()

    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_db] = fake_db
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_regular_user_cannot_create_api_key():
    async with _client(_build_app(_user("trial"))) as client:
        response = await client.post("/api/user/apikey/", json={"name": "脚本"})
    assert response.status_code == 403
    assert response.json()["detail"] == "需要管理员权限"


async def test_regular_user_cannot_regenerate_api_key():
    async with _client(_build_app(_user("trial"))) as client:
        response = await client.post("/api/user/apikey/1/regenerate")
    assert response.status_code == 403


async def test_regular_user_cannot_approve_cli_login(monkeypatch):
    called = False

    async def fake_approve(db, user_code, user):
        nonlocal called
        called = True
        return SimpleNamespace(to_dict=lambda: {})

    monkeypatch.setattr(auth_router, "approve_cli_auth_session", fake_approve)
    async with _client(_build_app(_user("trial"))) as client:
        response = await client.post("/api/auth/cli/sessions/ABCD-1234/approve")
    assert response.status_code == 403
    assert called is False


async def test_admin_still_creates_api_key(monkeypatch):
    monkeypatch.setattr(
        user_router.AuthUtils, "generate_api_key", staticmethod(lambda: ("yxkey_abc123secret", "hash", "yxkey_abc"))
    )
    session = _FakeSession()
    async with _client(_build_app(_user("boss", role="admin"), session)) as client:
        response = await client.post("/api/user/apikey/", json={"name": "生产环境"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["secret"] == "yxkey_abc123secret"
    assert body["api_key"]["name"] == "生产环境"
    assert body["api_key"]["user_id"] == 1
    assert len(session.added) == 1


async def test_admin_can_approve_cli_login(monkeypatch):
    async def fake_approve(db, user_code, user):
        assert user.role == "admin"
        return SimpleNamespace(
            to_dict=lambda: {
                "id": 1,
                "user_code": user_code,
                "status": "approved",
                "key_name": "cli",
                "approved_user_id": user.id,
                "api_key_id": None,
                "created_at": None,
                "expires_at": None,
                "approved_at": None,
                "consumed_at": None,
            }
        )

    monkeypatch.setattr(auth_router, "approve_cli_auth_session", fake_approve)
    async with _client(_build_app(_user("boss", role="admin"))) as client:
        response = await client.post("/api/auth/cli/sessions/ABCD-1234/approve")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
