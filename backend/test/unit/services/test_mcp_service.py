from __future__ import annotations

from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.mcp import service as mcp_service
from yuxi.storage.postgres import manager as postgres_manager
from yuxi.storage.postgres.models_business import MCPServer


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


@pytest_asyncio.fixture
async def mcp_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(MCPServer.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


class _FakeClient:
    def __init__(self, tools):
        self._tools = tools

    async def get_tools(self):
        return self._tools


async def test_ensure_builtin_mcp_servers_removes_retired_system_server(monkeypatch, mcp_session):
    retired_server = MCPServer(
        slug="sequentialthinking",
        name="sequentialthinking",
        description="old builtin",
        transport="streamable_http",
        url="https://remote.mcpservers.org/sequentialthinking/mcp",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(retired_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    retired = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "sequentialthinking"))
    chart = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "mcp-server-chart"))
    assert retired is None
    assert chart is not None


async def test_ensure_builtin_mcp_servers_preserves_user_server_with_retired_slug(monkeypatch, mcp_session):
    user_server = MCPServer(
        slug="sequentialthinking",
        name="用户自定义 MCP",
        description="user managed",
        transport="streamable_http",
        url="https://example.com/mcp",
        enabled=1,
        created_by="admin",
        updated_by="admin",
    )
    mcp_session.add(user_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "sequentialthinking"))
    assert server is not None
    assert server.created_by == "admin"


async def test_get_enabled_mcp_tools_loads_latest_config_from_db(monkeypatch):
    captured: list[dict] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "demo"
        return {"transport": "stdio", "command": "demo", "disabled_tools": ["tool_b"]}

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, disabled_tools=None, **kwargs):
        del kwargs
        captured.append(
            {
                "server_name": server_name,
                "additional_servers": additional_servers,
                "disabled_tools": list(disabled_tools or []),
            }
        )
        return ["tool-a"]

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_enabled_mcp_tools("demo")

    assert tools == ["tool-a"]
    assert captured == [
        {
            "server_name": "demo",
            "additional_servers": {
                "demo": {"transport": "stdio", "command": "demo", "disabled_tools": ["tool_b"]}
            },
            "disabled_tools": ["tool_b"],
        }
    ]


async def test_get_mcp_tools_rebuilds_cache_when_config_hash_changes(monkeypatch):
    mcp_service.clear_mcp_cache()

    configs = [
        {"transport": "stdio", "command": "demo-v1", "disabled_tools": []},
        {"transport": "stdio", "command": "demo-v2", "disabled_tools": []},
    ]
    build_calls: list[str] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "demo"
        return configs[0]

    async def fake_get_mcp_client(server_configs):
        config = server_configs["demo"]
        build_calls.append(config["command"])
        tool = SimpleNamespace(name=f"tool_for_{config['command']}", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    tools_v1_first = await mcp_service.get_mcp_tools("demo")
    tools_v1_second = await mcp_service.get_mcp_tools("demo")

    configs[0] = configs[1]
    tools_v2 = await mcp_service.get_mcp_tools("demo")

    assert [tool.name for tool in tools_v1_first] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v1_second] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v2] == ["tool_for_demo-v2"]
    assert build_calls == ["demo-v1", "demo-v2"]

    mcp_service.clear_mcp_cache()


async def test_get_tools_from_all_servers_loads_names_from_db_once(monkeypatch):
    server_configs = {
        "alpha": {"transport": "stdio", "command": "cmd-a", "disabled_tools": []},
        "beta": {"transport": "stdio", "command": "cmd-b", "disabled_tools": []},
    }
    calls: list[tuple[str, dict[str, dict]]] = []

    async def fake_load_enabled_mcp_server_configs(*, names=None, db=None):
        del names, db
        return server_configs

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, **kwargs):
        del kwargs
        calls.append((server_name, additional_servers or {}))
        return [server_name]

    monkeypatch.setattr(mcp_service, "_load_enabled_mcp_server_configs", fake_load_enabled_mcp_server_configs)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_tools_from_all_servers()

    assert tools == ["alpha", "beta"]
    assert calls == [
        ("alpha", server_configs),
        ("beta", server_configs),
    ]


async def test_get_mcp_tools_sets_handle_tool_error(monkeypatch):
    mcp_service.clear_mcp_cache()

    config = {"transport": "stdio", "command": "demo-tool", "disabled_tools": []}

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        return config

    async def fake_get_mcp_client(server_configs):
        tool = SimpleNamespace(name="demo_tool", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    tools = await mcp_service.get_mcp_tools("demo")
    assert len(tools) == 1
    assert tools[0].handle_tool_error is True

    mcp_service.clear_mcp_cache()
