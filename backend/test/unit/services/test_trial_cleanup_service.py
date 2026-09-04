"""试用账号加固：每晚清空 trial 名下内容（0903 rxyy 拍板「配额 + 每晚清空」）。

事故背景：trial 是所有访客共用的普通账号，访客 A 建的库 / 对话 / 智能体访客 B 全能看到还能删。
这里锁住三件事：只清 role=user 的账号（配置手滑写了管理员也不会清）、硬删对话时按外键顺序
（messages 引用 agent_runs、agent_runs 引用 conversations，顺序错了就是 FK 违约）、worker 里
按 Asia/Shanghai 注册了 cron。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import yuxi
from sqlalchemy.sql import Delete, Select
from sqlalchemy.sql.elements import TextClause

from yuxi.services import trial_cleanup_service as svc
from yuxi.storage.postgres.models_business import Agent, Conversation, User
from yuxi.storage.postgres.models_knowledge import KnowledgeBase

pytestmark = pytest.mark.asyncio


class _Result:
    def __init__(self, rows=None, scalars=None, rowcount=0):
        self._rows = rows or []
        self._scalars = scalars or []
        self.rowcount = rowcount

    def all(self):
        return self._rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalars))

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None


class _FakeSession:
    """记录执行过的语句；按 select 的实体 / delete 的表名返回预置数据。"""

    def __init__(self, *, conversations=(), agents=(), kb_ids=(), users=()):
        self.conversations = list(conversations)
        self.agents = list(agents)
        self.kb_ids = list(kb_ids)
        self.users = list(users)
        self.deleted_tables: list[str] = []
        self.text_sql: list[str] = []
        self.orm_deleted: list = []
        self.commits = 0

    async def execute(self, stmt, params=None):
        if isinstance(stmt, Delete):
            self.deleted_tables.append(stmt.table.name)
            return _Result(rowcount=2 if stmt.table.name == "agent_runs" else 1)
        if isinstance(stmt, TextClause):
            self.text_sql.append(str(stmt))
            return _Result()
        if isinstance(stmt, Select):
            entity = stmt.column_descriptions[0]["entity"]
            if entity is Conversation:
                return _Result(rows=[(c.id, c.thread_id) for c in self.conversations])
            if entity is Agent:
                return _Result(scalars=self.agents)
            if entity is KnowledgeBase:
                return _Result(rows=[(kb_id,) for kb_id in self.kb_ids])
            if entity is User:
                return _Result(scalars=self.users)
        raise AssertionError(f"unexpected statement: {stmt}")

    async def delete(self, obj):
        self.orm_deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


def _session_ctx(session):
    @asynccontextmanager
    async def ctx():
        yield session

    return ctx


class _FakeKnowledgeBase:
    def __init__(self):
        self.deleted: list[str] = []

    async def delete_database(self, kb_id: str):
        self.deleted.append(kb_id)
        return {"message": "删除成功"}


async def test_cleanup_user_data_deletes_everything_in_fk_safe_order(monkeypatch, tmp_path):
    threads_root = tmp_path / "threads"
    (threads_root / "t1" / "user-data").mkdir(parents=True)
    (threads_root / "shared" / "trial" / "workspace").mkdir(parents=True)
    (threads_root / "keep-me").mkdir(parents=True)
    monkeypatch.setattr(svc, "_threads_root", lambda: threads_root)

    fake_kb = _FakeKnowledgeBase()
    monkeypatch.setattr(yuxi, "knowledge_base", fake_kb, raising=False)
    reloaded = []

    async def fake_reload_all():
        reloaded.append(True)

    from yuxi.agents.buildin import agent_manager

    monkeypatch.setattr(agent_manager, "reload_all", fake_reload_all)

    kb_session = _FakeSession(kb_ids=["kb_a", "kb_b"])
    monkeypatch.setattr(svc.pg_manager, "get_async_session_context", _session_ctx(kb_session))

    agents = [
        SimpleNamespace(slug="agent-trial-1", is_default=False),
        SimpleNamespace(slug="agent-default", is_default=True),
    ]
    session = _FakeSession(
        conversations=[SimpleNamespace(id=1, thread_id="t1"), SimpleNamespace(id=2, thread_id="t2-no-dir")],
        agents=agents,
    )
    user = SimpleNamespace(id=22, uid="trial", username="trial", role="user")

    summary = await svc.cleanup_user_data(session, user)

    assert fake_kb.deleted == ["kb_a", "kb_b"]
    assert summary["knowledge_bases"] == ["kb_a", "kb_b"]
    assert reloaded == [True]

    assert summary["agents"] == ["agent-trial-1"]
    assert [a.slug for a in session.orm_deleted] == ["agent-trial-1"]

    assert session.deleted_tables == [
        "tool_calls",
        "message_feedbacks",
        "messages",
        "conversation_stats",
        "agent_runs",
        "subagent_threads",
        "conversations",
        "api_keys",
        "user_config",
    ]
    assert [sql.split()[2] for sql in session.text_sql] == ["checkpoint_writes", "checkpoint_blobs", "checkpoints"]
    assert all("thread_id IN" in sql for sql in session.text_sql)

    assert summary["conversations"] == 2
    assert summary["runs"] == 2
    assert summary["thread_dirs_removed"] == 1
    assert not (threads_root / "t1").exists()
    assert (threads_root / "keep-me").exists()
    assert summary["workspace_removed"] is True
    assert not (threads_root / "shared" / "trial").exists()
    assert summary["api_keys"] == 1
    assert summary["memory_reset"] is True


async def test_cleanup_trial_users_skips_admins_and_unknown_accounts(monkeypatch):
    admin = SimpleNamespace(id=1, uid="admin", username="admin", role="superadmin")
    sessions = {
        "admin": _FakeSession(users=[admin]),
        "ghost": _FakeSession(users=[]),
    }
    order = iter(["admin", "ghost"])

    @asynccontextmanager
    async def ctx():
        yield sessions[next(order)]

    monkeypatch.setattr(svc.pg_manager, "get_async_session_context", ctx)

    async def must_not_run(db, user):
        raise AssertionError("cleanup_user_data must not run for admin / unknown users")

    monkeypatch.setattr(svc, "cleanup_user_data", must_not_run)

    result = await svc.cleanup_trial_users(["admin", "ghost"])
    assert result["results"] == [
        {"username": "admin", "skipped": "not_regular_user"},
        {"username": "ghost", "skipped": "not_found"},
    ]
    assert result["cleaned_at"].endswith("Z")


async def test_cleanup_trial_users_runs_for_regular_user(monkeypatch):
    trial = SimpleNamespace(id=22, uid="trial", username="trial", role="user")
    session = _FakeSession(users=[trial])
    monkeypatch.setattr(svc.pg_manager, "get_async_session_context", _session_ctx(session))

    async def fake_cleanup(db, user):
        assert db is session and user is trial
        return {"username": user.username, "conversations": 3}

    monkeypatch.setattr(svc, "cleanup_user_data", fake_cleanup)
    result = await svc.cleanup_trial_users(["trial"])
    assert result["results"] == [{"username": "trial", "conversations": 3}]


async def test_worker_registers_nightly_cron_in_shanghai_time():
    from yuxi.services.run_worker import WorkerSettings
    from yuxi.utils.datetime_utils import SHANGHAI_TZ

    assert WorkerSettings.timezone is SHANGHAI_TZ
    jobs = {job.name: job for job in WorkerSettings.cron_jobs}
    job = jobs["cron:run_trial_cleanup_job"]
    assert job.hour == svc.TRIAL_CLEANUP_HOUR
    assert job.minute == svc.TRIAL_CLEANUP_MINUTE
    assert job.coroutine is svc.run_trial_cleanup_job


async def test_assistant_message_total_tokens_comes_from_usage_metadata():
    from yuxi.services.chat_service import message_total_tokens

    assert (
        message_total_tokens({"usage_metadata": {"input_tokens": 9491, "output_tokens": 138, "total_tokens": 9629}})
        == 9629
    )
    assert message_total_tokens({"usage_metadata": {"input_tokens": 1}}) is None
    assert message_total_tokens({"usage_metadata": {"total_tokens": "9629"}}) is None
    assert message_total_tokens({"content": "no usage"}) is None
    assert message_total_tokens(None) is None
