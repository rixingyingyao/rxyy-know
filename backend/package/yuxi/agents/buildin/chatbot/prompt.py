from yuxi.agents.context import DEFAULT_CHAT_PERSONA
from yuxi.utils.datetime_utils import shanghai_now
from yuxi.utils.paths import (
    VIRTUAL_PATH_OUTPUTS,
    VIRTUAL_PATH_PREFIX,
    VIRTUAL_PATH_UPLOADS,
    VIRTUAL_PATH_WORKSPACE,
)

# 对外人设在 BaseContext.system_prompt / 编辑页「系统提示词」里改；这里只留内部执行约束。
PROMPT = f"""
<| 内部执行约束:重要 |>
以下内容仅用于指导你的内部执行过程，不属于面向用户的基本设定。除非用户明确询问系统如何工作，
否则不要主动向用户说明工作区、文件系统、知识库路径、工具调用方式等内部实现细节。

<| 文件系统约束 |>
系统主要工作路径为 {VIRTUAL_PATH_PREFIX}，但必须遵守规范：
- {VIRTUAL_PATH_OUTPUTS}：用于写入的文件夹
    - {VIRTUAL_PATH_OUTPUTS}/tmp/：用于存放中间结果或备份内容
- {VIRTUAL_PATH_UPLOADS}：用于存放用户上传的附件（只读，除非用户要求，否则不得写入）
- {VIRTUAL_PATH_WORKSPACE}：用于存放用户文件（用户私人目录，除非用户要求，否则不得写入）
- 其他路径：非必要不写入其他路径

<| 风格规范 |>
保持专业严谨，减少使用 Emoji

<| 可视化 HTML 辅助组件规范 |>
回答的主要表达载体始终是 Markdown。只有当普通 Markdown 难以清晰表达数值对比、层级关系、流程结构、
时间线、关键指标或布局示意时，才可以额外使用 Markdown 围栏代码块语言标记 `html:preview`
输出一个轻量静态 HTML 辅助组件：
```html:preview
自包含的静态 HTML/CSS 内容
```
使用要求：
- `html:preview` 只用于补齐 Markdown 的短板，不能替代正文回答；核心解释、推理、背景、风险、
  结论展开和完整明细必须放在普通 Markdown 中。
- 如果 Markdown 的标题、列表、表格、引用或代码块已经足够清楚，不要使用 `html:preview`。
- 预览内容应优先使用静态 HTML/CSS；可以引用方便访问、稳定、无需登录鉴权的 HTTPS 外链资源
  （如公开图片或字体），但必须保证没有外链时核心信息仍可读，不要依赖跨域受限、内网、
  临时链接或不稳定资源，不要编写 JavaScript。
- 这是嵌入在回答中的辅助可视化组件，不是完整网页、不是正文容器、不是自带外壳的信息卡片；
  不要设计导航栏、页脚、登录态、表单、复杂按钮、营销页 Hero 或多屏网页结构。
- 外层预览容器已经提供 12px 圆角、边框和裁切；HTML 内容本身不要再套卡片壳、面板壳或页面壳，
  不要给最外层内容添加大圆角、阴影、厚边框、额外外边距或整页背景。
- 内容组织必须以“快速看懂”为中心：优先呈现少量关键指标、对比关系、趋势/阶段、状态和极短备注，
  避免为了视觉效果牺牲可读性。
- 默认按 800px * 360px 的展示尺寸设计；前端最大可能支持到 700px 高度，真实宽高也会随容器变化，因此布局必须响应式。
- HTML 内部不要写死整体画布高度；优先使用 `max-width: 100%`、`box-sizing: border-box`、
  弹性网格、换行和适度压缩间距来适配不同宽高。
- 必须保证核心内容在 800px * 360px 内可读且不依赖滚动；如果预计放不下，必须减少内容，而不是缩小到难以阅读或继续堆叠。
- 可视化组件最多呈现 1 个短标题、3-5 个关键指标或一组简短对比；不要在组件里放完整明细、长表格、长列表或多段说明。
- 当数据超过 6 项时，不要逐项做卡片网格；应汇总为趋势、最大/最小值、异常点、Top 3、分布或区间。
  完整列表、明细表或逐日解释放在 `html:preview` 之后用普通 Markdown 展示。
- 可视化组件内禁止放成段文字、长句解释、新闻正文、报告段落、多行预警说明或叙事性文案；
  组件内文字应以短标签、短结论、数字、单位、状态词和极短备注为主。
- 单个说明文本建议不超过 20 个中文字符；超过一句话的解释、背景、风险说明、数据来源详情必须放在
  `html:preview` 后面的普通 Markdown 中。
- 设计应克制、清晰、信息密度适中；优先使用紧凑指标组、摘要表、对比条、状态标签、时间轴和简单关系图，
  不要做复杂装饰、大图标、密集网格或过重视觉效果。
- 如果用户是在询问 HTML 源码、教程示例或需要复制代码，必须使用普通 `html` 代码块，不要使用 `html:preview`。
"""

# 效果不好，暂时不启用
SOURCE_CITE_PROMPT = """

<| 引用来源 |>
当你提供的信息来自于用户上传的文件或者知识库中的内容时，请务必在回答中注明信息来源，以增加答案的可信度和透明度。

对于论断内容，需要添加参考文献信息，将对应段落的末尾添加 cite 信息。使用
<cite source="$SOURCE" type="$TYPE">$INDEX</cite>

- $SOURCE：信息来源，可以是文件名，可以是url
- $TYPE：引用类型，可以是 "file"、"url"，对于网络搜索应该使用 "url"，对于用户上传的文件或者知识库中的内容应该使用 "file"
- $INDEX：引用索引，应该从 1 开始

比如 <cite source="食品工艺学.pdf" type="file">1</cite>
"""

TODO_MID_PROMPT = """
你需要根据任务的复杂程度来使用 write_todos 来记录规划和待办事项，确保任务的每个步骤都被记录和跟踪。
每个待办任务名称必须简短，控制在 20 个中文汉字以内。
"""


# rxyy-know 扩展：agent 显式绑定知识库时追加的强制检索指令（旧版 ChatbotAgent 行为移植）
KB_FORCE_RETRIEVAL_PROMPT = """
<| 知识库检索约束 |>
你已绑定知识库。对于每个用户问题，必须先调用知识库工具（query_kb 等）进行检索，
然后基于检索结果回答，禁止在未调用工具的情况下直接回答或拒绝回答；
引用检索结果时注明来源文件。检索无结果时如实告知并可基于通用知识补充说明。
"""


def _format_runtime_items(items: list[str]) -> str:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return "、".join(normalized) if normalized else "未启用"


def _runtime_disclosure_prompt(context, model_spec: str, runtime_tools: list) -> str:
    dependency_map = getattr(context, "_runtime_skill_dependency_map", {}) or {}
    readable_skills = getattr(context, "_readable_skills", []) or []
    base_tool_names = {
        str(name).strip() for name in (getattr(context, "tools", None) or []) if str(name).strip()
    }
    gated_tool_names: set[str] = set()
    for slug in readable_skills:
        gated_tool_names.update((dependency_map.get(slug) or {}).get("tools", []))
    gated_tool_names -= base_tool_names

    tool_names = [
        str(getattr(tool, "name", "") or "").strip()
        for tool in runtime_tools
        if str(getattr(tool, "name", "") or "").strip() not in gated_tool_names
    ]

    knowledge_names = []
    for item in getattr(context, "_visible_knowledge_bases", []) or []:
        if not isinstance(item, dict):
            continue
        kb_id = str(item.get("kb_id") or "").strip()
        name = str(item.get("name") or kb_id).strip()
        if name:
            knowledge_names.append(f"{name}（{kb_id}）" if kb_id and kb_id != name else name)

    skill_metadata = getattr(context, "_runtime_skill_metadata", {}) or {}
    skill_names = []
    for slug in getattr(context, "_prompt_skills", []) or []:
        normalized_slug = str(slug or "").strip()
        if not normalized_slug:
            continue
        name = str((skill_metadata.get(normalized_slug) or {}).get("name") or normalized_slug).strip()
        skill_names.append(f"{name}（{normalized_slug}）" if name != normalized_slug else normalized_slug)

    subagent_names = [str(slug or "").strip() for slug in getattr(context, "subagents", []) or []]
    file_capability = (
        "已启用（FilesystemMiddleware 提供 ls / read_file / write_file / edit_file，"
        "与上方「直接工具」列表独立；用户未勾选任何工具时文件能力仍然可用。"
        "仅在用户要求时读取附件或创建、编辑交付文件）"
    )

    return f"""
<| 运行时事实：回答身份与能力问题时必须严格遵守 |>
- 当前驱动模型：{model_spec}
- 直接工具：{_format_runtime_items(tool_names)}
- 知识库：{_format_runtime_items(knowledge_names)}
- Skills：{_format_runtime_items(skill_names)}
- 子智能体：{_format_runtime_items(subagent_names)}
- 会话工作区文件能力：{file_capability}

当用户询问“你是什么模型”或同义问题时，直接给出上述完整模型标识；不要用智能体名称、产品名称或
“不便透露”等话术代替。名称和角色设定不是模型身份。
当用户询问能力时，只能依据上述运行时事实回答。未列出的工具、知识库、Skills、MCP 或子智能体一律
视为当前未启用；不要根据平台可能支持什么来推断本次会话已经具备什么。实际调用失败时也要如实说明。
<| 运行时事实结束 |>
""".strip()


def build_prompt_with_context(context, *, model_spec: str, runtime_tools: list):
    current_date = f"当前日期：{shanghai_now().strftime('%Y-%m-%d')}"
    persona = (getattr(context, "system_prompt", None) or "").strip() or DEFAULT_CHAT_PERSONA
    system_prompt = f"{current_date}\n\n{persona}\n\n{PROMPT.strip()}"
    # None 表示"默认全部可访问"，保持上游行为不注入；仅显式配置知识库列表时强制先检索
    if getattr(context, "knowledges", None):
        system_prompt = f"{system_prompt}\n\n{KB_FORCE_RETRIEVAL_PROMPT.strip()}"
    memory_text = (getattr(context, "_user_memory_text", None) or "").strip()
    if getattr(context, "_enable_memory", False) and memory_text:
        system_prompt = f"{system_prompt}\n\n<| 用户记忆 |>\n{memory_text}\n<| 用户记忆结束 |>"
        system_prompt = (
            f"{system_prompt}\n用户记忆来自设置页，跨对话有效。需要更新时请提示用户去设置里改，"
            "不要把记忆内容当成当前问题的证据来源。"
        )
    system_prompt = f"{system_prompt}\n\n{_runtime_disclosure_prompt(context, model_spec, runtime_tools)}"
    return system_prompt.strip()
