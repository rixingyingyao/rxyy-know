from types import SimpleNamespace

from yuxi.agents.buildin.chatbot.prompt import build_prompt_with_context


def _context(**overrides):
    values = {
        "system_prompt": "请使用简体中文。",
        "knowledges": [],
        "skills": [],
        "subagents": [],
        "_visible_knowledge_bases": [],
        "_prompt_skills": [],
        "_runtime_skill_metadata": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_model_identity_incident_injects_exact_runtime_model_without_fixed_alias():
    prompt = build_prompt_with_context(
        _context(),
        model_spec="alibaba:qwen3.7-plus",
        runtime_tools=[],
    )

    assert "alibaba:qwen3.7-plus" in prompt
    assert "语析" not in prompt
    assert "直接给出上述完整模型标识" in prompt


def test_capability_hallucination_incident_lists_only_enabled_runtime_resources():
    context = _context(
        knowledges=["kb-1"],
        skills=["deep-research"],
        subagents=["researcher"],
        _visible_knowledge_bases=[{"kb_id": "kb-1", "name": "产品资料库"}],
        _prompt_skills=["deep-research"],
        _runtime_skill_metadata={
            "deep-research": {
                "name": "深度研究",
                "description": "多来源调研",
                "path": "/home/gem/skills/deep-research/SKILL.md",
            }
        },
    )
    tools = [SimpleNamespace(name="web_search"), SimpleNamespace(name="calculator")]

    prompt = build_prompt_with_context(
        context,
        model_spec="provider:model",
        runtime_tools=tools,
    )

    assert "web_search、calculator" in prompt
    assert "产品资料库（kb-1）" in prompt
    assert "深度研究（deep-research）" in prompt
    assert "researcher" in prompt
    assert "MySQL" not in prompt


def test_capability_hallucination_incident_marks_unavailable_resources_as_disabled():
    prompt = build_prompt_with_context(
        _context(),
        model_spec="provider:model",
        runtime_tools=[],
    )

    assert "直接工具：未启用" in prompt
    assert "知识库：未启用" in prompt
    assert "Skills：未启用" in prompt
    assert "子智能体：未启用" in prompt
    assert "会话工作区文件能力：已启用" in prompt
    assert "FilesystemMiddleware" in prompt
    assert "MySQL" not in prompt
    assert "网页搜索" not in prompt


def test_skill_gated_tools_are_omitted_from_direct_tool_list_until_activated():
    context = _context(
        tools=[],
        _readable_skills=["knowledge-base"],
        _prompt_skills=["knowledge-base"],
        _runtime_skill_dependency_map={
            "knowledge-base": {"tools": ["query_kb", "list_kbs"]},
        },
        _runtime_skill_metadata={
            "knowledge-base": {"name": "知识库"},
        },
    )
    tools = [
        SimpleNamespace(name="calculator"),
        SimpleNamespace(name="query_kb"),
        SimpleNamespace(name="list_kbs"),
    ]

    prompt = build_prompt_with_context(
        context,
        model_spec="alibaba:qwen3.7-plus",
        runtime_tools=tools,
    )
    direct_tools_line = next(
        line for line in prompt.splitlines() if line.startswith("- 直接工具：")
    )

    assert direct_tools_line == "- 直接工具：calculator"
    assert "query_kb" not in direct_tools_line
    assert "list_kbs" not in direct_tools_line
    assert "知识库（knowledge-base）" in prompt
    assert "alibaba:qwen3.7-plus" in prompt
