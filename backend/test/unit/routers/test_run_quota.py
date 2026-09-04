"""试用账号加固：普通用户的每日对话配额（0903 rxyy 拍板「配额 + 每晚清空」）。
事故背景：`POST /api/agent/runs` 对 user 角色原来没有任何次数 / token 限制，模型是 qwen3.7-plus、
摘要阈值 900K，共用的 trial 账号被脚本刷一晚就是真钱。这里锁住：管理员不受限、超限 429 且
带可读文案和 Retry-After、阈值 <=0 表示不限、自然日按 Asia/Shanghai 切。
"""
from __future__ import annotations
import datetime as dt
from types import SimpleNamespace
import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from server.utils import run_quota
from server.utils.auth_middleware import get_db, get_required_user
pytestmark = pytest.mark.asyncio
def _user(uid: str, role: str = "user"):
    return SimpleNamespace(id=1, uid=uid, role=role, department_id=1)
def _install_usage(monkeypatch, messages_used: int, tokens_used: int):
    async def fake_usage(db, uid, day_start):
        del db, uid, day_start
        return messages_used, tokens_used
    monkeypatch.setattr(run_quota, "get_daily_usage", fake_usage)
async def test_quota_day_window_is_shanghai_midnight_in_naive_utc():
    now = dt.datetime(2026, 9, 3, 12, 30, tzinfo=dt.UTC)  # 北京时间 09-03 20:30
    day_start, day_end = run_quota.quota_day_window(now)
    assert day_start == dt.datetime(2026, 9, 2, 16, 0)  # 09-03 00:00 CST
    assert day_end == dt.datetime(2026, 9, 3, 16, 0)  # 09-04 00:00 CST
    assert day_start.tzinfo is None and day_end.tzinfo is None
    # 北京时间 09-03 01:00 仍属于 09-03（UTC 还是 09-02 17:00）
    early = dt.datetime(2026, 9, 2, 17, 0, tzinfo=dt.UTC)
    assert run_quota.quota_day_window(early)[0] == day_start
async def test_admin_is_never_limited(monkeypatch):
    monkeypatch.setattr(run_quota, "USER_DAILY_MESSAGE_LIMIT", 1)
    monkeypatch.setattr(run_quota, "USER_DAILY_TOKEN_LIMIT", 1)
    _install_usage(monkeypatch, messages_used=999, tokens_used=999_999)
    for role in ("admin", "superadmin"):
        user = _user("boss", role=role)
        assert await run_quota.ensure_run_quota(current_user=user, db=object()) is user
async def test_user_under_both_limits_passes(monkeypatch):
    monkeypatch.setattr(run_quota, "USER_DAILY_MESSAGE_LIMIT", 60)
    monkeypatch.setattr(run_quota, "USER_DAILY_TOKEN_LIMIT", 500_000)
    _install_usage(monkeypatch, messages_used=59, tokens_used=499_999)
    user = _user("trial")
    assert await run_quota.ensure_run_quota(current_user=user, db=object()) is user
async def test_user_at_message_limit_gets_429_with_reset_hint(monkeypatch):
    monkeypatch.setattr(run_quota, "USER_DAILY_MESSAGE_LIMIT", 60)
    monkeypatch.setattr(run_quota, "USER_DAILY_TOKEN_LIMIT", 500_000)
    _install_usage(monkeypatch, messages_used=60, tokens_used=10)
    with pytest.raises(HTTPException) as exc_info:
        await run_quota.ensure_run_quota(current_user=_user("trial"), db=object())
    exc = exc_info.value
    assert exc.status_code == 429
    assert exc.detail["code"] == "quota_exceeded"
    assert exc.detail["kind"] == "messages"
    assert exc.detail["limit"] == 60 and exc.detail["used"] == 60
    assert "今日对话次数已用完" in exc.detail["message"]
    assert "北京时间" in exc.detail["message"]
    assert exc.detail["reset_at"].endswith("Z")
    assert int(exc.headers["Retry-After"]) >= 1
async def test_user_at_token_limit_gets_429_kind_tokens(monkeypatch):
    monkeypatch.setattr(run_quota, "USER_DAILY_MESSAGE_LIMIT", 60)
    monkeypatch.setattr(run_quota, "USER_DAILY_TOKEN_LIMIT", 500_000)
    _install_usage(monkeypatch, messages_used=3, tokens_used=500_000)
    with pytest.raises(HTTPException) as exc_info:
        await run_quota.ensure_run_quota(current_user=_user("trial"), db=object())
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["kind"] == "tokens"
    assert "token" in exc_info.value.detail["message"]
async def test_non_positive_limits_disable_that_gate(monkeypatch):
    _install_usage(monkeypatch, messages_used=10_000, tokens_used=10_000_000)
    monkeypatch.setattr(run_quota, "USER_DAILY_MESSAGE_LIMIT", 0)
    monkeypatch.setattr(run_quota, "USER_DAILY_TOKEN_LIMIT", 0)
    user = _user("trial")
    assert await run_quota.ensure_run_quota(current_user=user, db=object()) is user
    # 只关消息闸，token 闸仍生效
    monkeypatch.setattr(run_quota, "USER_DAILY_TOKEN_LIMIT", 1)
    with pytest.raises(HTTPException) as exc_info:
        await run_quota.ensure_run_quota(current_user=user, db=object())
    assert exc_info.value.detail["kind"] == "tokens"
async def test_quota_snapshot_shape(monkeypatch):
    monkeypatch.setattr(run_quota, "USER_DAILY_MESSAGE_LIMIT", 60)
    monkeypatch.setattr(run_quota, "USER_DAILY_TOKEN_LIMIT", 500_000)
    _install_usage(monkeypatch, messages_used=7, tokens_used=1234)
    snapshot = await run_quota.get_quota_snapshot(object(), _user("trial"))
    assert snapshot["unlimited"] is False
    assert snapshot["messages"] == {"limit": 60, "used": 7}
    assert snapshot["tokens"] == {"limit": 500_000, "used": 1234}
    assert snapshot["reset_at"].endswith("Z")
    assert (await run_quota.get_quota_snapshot(object(), _user("boss", role="admin")))["unlimited"] is True
async def test_http_429_body_is_readable_for_frontend(monkeypatch):
    """前端 base.js 取 detail.message 展示，这里锁住 429 的响应体形状。"""
    monkeypatch.setattr(run_quota, "USER_DAILY_MESSAGE_LIMIT", 2)
    monkeypatch.setattr(run_quota, "USER_DAILY_TOKEN_LIMIT", 0)
    _install_usage(monkeypatch, messages_used=2, tokens_used=0)
    app = FastAPI()
    @app.post("/runs")
    async def create_run(current_user=Depends(run_quota.ensure_run_quota)):
        return {"ok": True, "uid": current_user.uid}
    async def fake_db():
        yield object()
    app.dependency_overrides[get_required_user] = lambda: _user("trial")
    app.dependency_overrides[get_db] = fake_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/runs")
    assert response.status_code == 429
    body = response.json()["detail"]
    assert body["code"] == "quota_exceeded"
    assert body["message"].startswith("今日对话次数已用完（2/2）")
    assert "Retry-After" in response.headers
