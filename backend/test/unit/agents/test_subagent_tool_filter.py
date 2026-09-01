from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.agents.buildin.subagent import graph as subagent_graph


class _Request:
    def __init__(self, tools):
        self.tools = tools

    def override(self, **kwargs):
        return _Request(kwargs.get("tools", self.tools))


def test_filter_disabled_tools_keeps_allowed_tools_order():
    tools = [
        SimpleNamespace(name="search"),
        SimpleNamespace(name="present_artifacts"),
        {"name": "ask_user_question"},
        SimpleNamespace(name="install_skill"),
        SimpleNamespace(name="calculator"),
    ]

    filtered = subagent_graph._filter_disabled_tools(tools)

    assert [subagent_graph._tool_name(tool) for tool in filtered] == ["search", "calculator"]


def test_subagent_tool_filter_middleware_filters_before_handler():
    middleware = subagent_graph._SubAgentToolFilterMiddleware()
    seen = {}

    def handler(request):
        seen["tools"] = request.tools
        return "ok"

    result = middleware.wrap_model_call(
        _Request([
            SimpleNamespace(name="present_artifacts"),
            SimpleNamespace(name="allowed_tool"),
        ]),
        handler,
    )

    assert result == "ok"
    assert [tool.name for tool in seen["tools"]] == ["allowed_tool"]


@pytest.mark.asyncio
async def test_subagent_tool_filter_middleware_filters_async_before_handler():
    middleware = subagent_graph._SubAgentToolFilterMiddleware()
    seen = {}

    async def handler(request):
        seen["tools"] = request.tools
        return "ok"

    result = await middleware.awrap_model_call(
        _Request([
            {"name": "ask_user_question"},
            SimpleNamespace(name="allowed_tool"),
        ]),
        handler,
    )

    assert result == "ok"
    assert [subagent_graph._tool_name(tool) for tool in seen["tools"]] == ["allowed_tool"]


@pytest.mark.asyncio
async def test_subagent_get_info_hides_disabled_tool_options(monkeypatch):
    async def get_info(_self, **_kwargs):
        return {
            "metadata": {},
            "configurable_items": {
                "tools": {
                    "options": [
                        {"key": "present_artifacts", "name": "展示交付物"},
                        {"key": "allowed_tool", "name": "Allowed"},
                        {"key": "ask_user_question", "name": "向用户提问"},
                        {"key": "install_skill", "name": "安装技能"},
                    ]
                }
            },
        }

    monkeypatch.setattr(subagent_graph.BaseAgent, "get_info", get_info)

    info = await subagent_graph.SubAgentBackend().get_info()

    assert [option["key"] for option in info["configurable_items"]["tools"]["options"]] == ["allowed_tool"]
