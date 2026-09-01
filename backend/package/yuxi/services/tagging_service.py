"""
自动打标服务

基于 LLM + 规则引擎的混合打标方案：
- 规则引擎：关键词/正则匹配，确定性高，速度快
- LLM 打标：语义理解，处理模糊/长尾内容
- 合并去重：规则优先，LLM 补充

标签体系：IPTC Media Topics L1-L3 + 中国广电特色补充（共 ~1059 节点）
打标策略：尽可能打到最深层级，存储完整路径，对外展示 L2
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from yuxi.utils import logger

# ============================================================
# 数据模型
# ============================================================

TAXONOMY_FILE = Path(__file__).parent / "tagging" / "taxonomy_data.json"


# 维度 ID 前缀映射
DIMENSION_PREFIX_MAP: dict[str, str] = {
    "DT": "tone",
    "DI": "type",
    "DA": "audience",
}


def _infer_dimension(node_id: str, explicit: str | None = None) -> str:
    """从节点 ID 前缀推断维度，优先使用显式声明"""
    if explicit:
        return explicit
    for prefix, dim in DIMENSION_PREFIX_MAP.items():
        if node_id.startswith(prefix):
            return dim
    return "topic"


class TaxonomyNode:
    """标签节点"""

    __slots__ = ("id", "name_zh", "name_en", "level", "parent_id", "path", "source", "dimension", "children_ids")

    def __init__(self, data: dict):
        self.id: str = data["id"]
        self.name_zh: str = data["name_zh"]
        self.name_en: str = data.get("name_en", "")
        self.level: int = data["level"]
        self.parent_id: str | None = data.get("parent_id")
        self.path: str = data.get("path", self.name_zh)
        self.source: str = data.get("source", "IPTC")
        self.dimension: str = _infer_dimension(self.id, data.get("dimension"))
        self.children_ids: list[str] = []


class TagResult:
    """单个标签结果"""

    __slots__ = ("tag_id", "tag_name", "path", "level", "confidence", "source", "reasoning")

    def __init__(
        self,
        tag_id: str,
        tag_name: str,
        path: str,
        level: int,
        confidence: float,
        source: str,
        reasoning: str = "",
        **_kwargs,
    ):
        self.tag_id = tag_id
        self.tag_name = tag_name
        self.path = path
        self.level = level
        self.confidence = confidence
        self.source = source  # "rules" | "llm"
        self.reasoning = reasoning

    def to_dict(self) -> dict:
        return {
            "tag_id": self.tag_id,
            "tag_name": self.tag_name,
            "path": self.path,
            "level": self.level,
            "confidence": self.confidence,
            "source": self.source,
            "reasoning": self.reasoning,
        }


# ============================================================
# 规则引擎
# ============================================================

# 关键词 → 标签ID映射（高频确定性标签）
KEYWORD_RULES: list[dict] = [
    # 党建
    {"keywords": ["党史学习", "党史教育"], "tag_id": "CN0101", "tag_name": "党史学习教育"},
    {"keywords": ["主题教育", "学习教育活动"], "tag_id": "CN0102", "tag_name": "主题教育活动"},
    {"keywords": ["反腐", "廉政", "纪检", "巡视", "党风廉政"], "tag_id": "CN0103", "tag_name": "党风廉政建设"},
    {"keywords": ["基层党建", "党支部", "党员发展"], "tag_id": "CN0104", "tag_name": "基层党建"},
    # 两会
    {"keywords": ["全国两会", "全国人大", "全国政协"], "tag_id": "CN0201", "tag_name": "全国两会"},
    {"keywords": ["地方两会", "省人大", "市人大", "省政协", "市政协"], "tag_id": "CN0202", "tag_name": "地方两会"},
    {"keywords": ["政府工作报告"], "tag_id": "CN0203", "tag_name": "政府工作报告"},
    # 乡村振兴
    {"keywords": ["乡村振兴", "振兴乡村"], "tag_id": "CN03", "tag_name": "乡村振兴"},
    {"keywords": ["脱贫攻坚", "脱贫成果", "巩固脱贫"], "tag_id": "CN0306", "tag_name": "脱贫攻坚成果巩固"},
    # 文明实践
    {"keywords": ["志愿服务", "志愿者"], "tag_id": "CN0401", "tag_name": "志愿服务"},
    {"keywords": ["道德模范", "好人好事"], "tag_id": "CN0402", "tag_name": "道德模范/好人好事"},
    {"keywords": ["文明城市", "创文"], "tag_id": "CN0403", "tag_name": "文明城市创建"},
    # 应急广播
    {"keywords": ["气象预警", "暴雨预警", "台风预警", "高温预警", "寒潮预警"], "tag_id": "CN0901", "tag_name": "气象预警"},
    {"keywords": ["地质灾害", "山体滑坡", "泥石流"], "tag_id": "CN0902", "tag_name": "地质灾害预警"},
    {"keywords": ["突发事件", "应急响应", "应急预案"], "tag_id": "CN0905", "tag_name": "突发事件应急"},
    # 国防军事
    {"keywords": ["征兵", "退役军人", "退伍军人"], "tag_id": "CN0604", "tag_name": "征兵/退役军人"},
    {"keywords": ["双拥", "拥军", "拥军优属"], "tag_id": "CN0602", "tag_name": "拥军优属/双拥"},
    # 非遗
    {"keywords": ["非物质文化遗产", "非遗", "民俗"], "tag_id": "CN0501", "tag_name": "非遗/民俗"},
]


def run_rules_engine(text: str, nodes_map: dict[str, TaxonomyNode]) -> list[TagResult]:
    """规则引擎：关键词匹配 + 标签名精确匹配"""
    results: list[TagResult] = []
    seen_ids: set[str] = set()
    text_lower = text.lower()

    # 1) 手工关键词规则（高优先级，confidence=1.0）
    for rule in KEYWORD_RULES:
        tag_id = rule["tag_id"]
        if tag_id in seen_ids:
            continue

        for kw in rule["keywords"]:
            if kw.lower() in text_lower:
                node = nodes_map.get(tag_id)
                path = node.path if node else rule["tag_name"]
                level = node.level if node else 2
                results.append(
                    TagResult(
                        tag_id=tag_id,
                        tag_name=rule["tag_name"],
                        path=path,
                        level=level,
                        confidence=1.0,
                        source="rules",
                        reasoning=f"关键词匹配: '{kw}'",
                    )
                )
                seen_ids.add(tag_id)
                break

    # 2) 标签名自动匹配（L3+ 节点，name_zh ≥ 3 字符，confidence=0.9）
    for node_id, node in nodes_map.items():
        if node_id in seen_ids or node.level < 3:
            continue
        name = node.name_zh
        if len(name) < 3:
            continue
        if name in text:
            results.append(
                TagResult(
                    tag_id=node_id,
                    tag_name=name,
                    path=node.path,
                    level=node.level,
                    confidence=0.9,
                    source="rules",
                    reasoning=f"标签名匹配: '{name}'",
                )
            )
            seen_ids.add(node_id)

    return results


# ============================================================
# LLM 打标 Prompt
# ============================================================

SYSTEM_PROMPT = """\
你是一个专业的新闻/媒体内容分类标注专家。你的任务是根据给定的标签体系，为输入的文本内容分配最精准的标签。

## 标签体系

以下是完整的标签分类树（缩进表示层级关系，格式：ID | 标签名）：
{taxonomy_tree}

## 核心标注原则

### 【最重要】深度优先 — 打到最符合内容的最深层级

你应该尽量选择标签体系中层级更深、更具体的标签，但前提是 **内容确实与该标签相关**。
- 如果内容足够具体，能匹配到 L3/L4 的叶子节点，就打到叶子节点
- 如果内容只涉及某个大方向，无法准确匹配到更深层级，那么打到最能准确描述的层级即可
- **核心原则：准确性优先，深度其次** — 不要为了追求深度而选择不相关的具体标签

判断规则：
- 如果一个标签有子标签，先检查子标签中是否有更准确的匹配
- 只有当子标签都不够匹配时，才允许选择父标签
- 宁可打到准确的 L2，也不要打到牵强的 L4

✅ 正确示例：
- 内容涉及"芭蕾舞表演" → 打 L4 的"芭蕾舞"，而非 L3"舞蹈"或 L2"表演艺术"（因为内容明确是芭蕾舞）
- 内容涉及"暴雨预警" → 打 L3 的"暴雨"或"气象预警"，而非 L2"天气"（因为可以更具体）
- 内容泛泛讨论"教育改革趋势" → 打 L2 的"教育政策"即可，不必强行匹配到某个具体教育类型

❌ 错误示例：
- 内容明确是"足球比赛"却只打了"体育"(L1) — 有更具体的标签却没选
- 内容是"各类运动综合报道"却强行打了"足球"(L3) — 为追求深度选了不准确的标签
- 内容涉及"房地产市场"却只打了"经济"(L1) — 有更深的匹配没有选

### 多维覆盖
从不同维度对内容进行全面标注：
- **内容主题**（最核心，必须有，应打到最深层级）
- **内容类型/形式**（DI 前缀，如访谈、纪录片、评论等）
- **情感基调/语气**（DT 前缀）
- **受众类型**（DA 前缀）

### 其他规则
- 标签数量：选择 3~10 个标签
- 允许建议新标签：tag_id 为 "__new__"
- 置信度标准：>0.9 非常确定, 0.7~0.9 比较确定, 0.5~0.7 可能相关, <0.5 不要输出

## 输出格式

严格输出 JSON 数组，不要输出其他内容：
```json
[
  {{"tag_id": "标签ID", "tag_name": "标签名", "confidence": 0.95, "reasoning": "理由"}},
  {{"tag_id": "__new__", "tag_name": "建议的新标签名", "confidence": 0.80, "reasoning": "理由", "suggested_parent": "建议挂载到的父标签ID或父标签名"}},
  ...
]
```

注意事项：
- 标签体系中已有的标签，必须使用对应的 tag_id
- 建议的新标签，tag_id 固定为 "__new__"，并通过 suggested_parent 指明在体系中的位置
- 每个标签必须附带简短的 reasoning 说明为什么选择该标签
- **再次强调：优先选择更深层级的标签，但必须确保标签与内容真正相关**"""

USER_PROMPT = """\
请为以下文本内容进行深度标注。
**关键要求**：每个标签必须打到标签体系中能匹配到的最深层级（叶子节点优先），禁止在有更深子标签可选时停留在浅层。

请覆盖以下维度：
1. **内容主题**（必须，核心话题、事件、人物等）→ 打到最深层级
2. **内容类型/形式**（DI 前缀，如评论、报道、访谈等）
3. **情感基调**（DT 前缀）
4. **受众类型**（DA 前缀，如适用）

---
{content}
---

请输出 JSON 数组（每个标签打到最深层级）："""

VIDEO_USER_PROMPT = """\
以下是一段视频的多模态分析结果，包含【画面描述】和【音频转写】两部分。
**注意：音频转写是视频中人物的实际发言和对话内容，包含大量语义信息（话题、人名、事件、观点），请务必深入分析。**

请从以下维度进行深度标注，每个标签必须打到标签体系中的最深层级（叶子节点或最具体层级）：

1. **音频语义**（最重要）：从音频转写中提取核心信息——
   - 谈论的具体话题/事件 → 对应主题标签的最深层级
   - 提到的人物/组织/机构
   - 表达的观点、立场
   - 涉及的行业/领域
2. **视觉内容**：画面场景、拍摄手法、关键物品
3. **人物**：出现或被提及的人物角色、身份、职业
4. **内容形式**：节目类型（访谈/纪录片/新闻播报/专题片等）
5. **情感/基调**：整体语气、情绪

标签数量：8~15 个，其中基于音频语义的标签不少于 3 个。

---
{content}
---

请输出 JSON 数组（每个标签必须打到最深层级）："""

AUDIO_USER_PROMPT = """\
以下是一段音频的转写文本。请深入分析音频中的语义内容进行标注。
**关键要求**：每个标签必须打到标签体系中能匹配到的最深层级。

请覆盖以下维度：
1. **核心话题**（音频讨论的具体话题/事件，不是笼统的领域）→ 打到最深层级
2. **人物/组织**（说话者身份、被提及的人物组织）→ 打到最深层级
3. **情感/基调**（语气、情绪、立场）
4. **内容形式**（访谈/讲座/播报/脱口秀等）
5. **行业/领域**（如适用）→ 打到最深层级

---
{content}
---

请输出 JSON 数组（每个标签打到最深层级）："""

MUSIC_USER_PROMPT = """\
以下是一段音乐/歌曲的描述。请从音乐维度进行标注：

1. **曲风/流派**（如：流行、摇滚、民谣、电子、古典、爵士等）→ 至少 L3
2. **情感/氛围**（如：欢快、忧伤、激昂、舒缓、怀旧等）
3. **乐器/编曲**（主要乐器或编曲特点，如有）
4. **语种/地域**（演唱语言或音乐风格地域特征，如有）

不要标注与音乐内容无关的新闻、行业等标签。

---
{content}
---

请输出 JSON 数组（标签至少 L3 深度）："""


# ============================================================
# 标签服务主类
# ============================================================


class TaggingService:
    """自动打标服务（类级缓存：多实例共享同一份标签体系数据）"""

    _nodes: list[TaxonomyNode] = []
    _nodes_map: dict[str, TaxonomyNode] = {}
    _name_zh_map: dict[str, TaxonomyNode] = {}  # 标签名 → 节点，用于快速精确匹配
    _l1_nodes: list[TaxonomyNode] = []
    _l2_nodes: list[TaxonomyNode] = []
    _taxonomy_tree_text: str = ""
    _loaded: bool = False

    def _ensure_loaded(self):
        """懒加载标签体系（类级缓存，只加载一次）"""
        if TaggingService._loaded:
            return
        self._load_taxonomy()

    def _load_taxonomy(self):
        """从 JSON 文件加载标签体系"""
        if not TAXONOMY_FILE.exists():
            logger.warning(f"Taxonomy file not found: {TAXONOMY_FILE}")
            return

        with open(TAXONOMY_FILE, encoding="utf-8") as f:
            data = json.load(f)

        cls = TaggingService
        nodes: list[TaxonomyNode] = []
        nodes_map: dict[str, TaxonomyNode] = {}
        name_zh_map: dict[str, TaxonomyNode] = {}
        l1_nodes: list[TaxonomyNode] = []
        l2_nodes: list[TaxonomyNode] = []

        for node_data in data["nodes"]:
            node = TaxonomyNode(node_data)
            nodes.append(node)
            nodes_map[node.id] = node
            name_zh_map[node.name_zh] = node

            if node.level == 1:
                l1_nodes.append(node)
            elif node.level == 2:
                l2_nodes.append(node)

        # Build children relationships
        for node in nodes:
            if node.parent_id and node.parent_id in nodes_map:
                nodes_map[node.parent_id].children_ids.append(node.id)

        # 一次性赋值到类变量
        cls._nodes = nodes
        cls._nodes_map = nodes_map
        cls._name_zh_map = name_zh_map
        cls._l1_nodes = l1_nodes
        cls._l2_nodes = l2_nodes
        cls._taxonomy_tree_text = self._build_taxonomy_text_from(nodes_map, l1_nodes)
        cls._loaded = True

        logger.info(
            f"Taxonomy loaded: {len(nodes)} nodes, "
            f"{len(l1_nodes)} L1, {len(l2_nodes)} L2"
        )

    @staticmethod
    def _build_taxonomy_text_from(nodes_map: dict[str, TaxonomyNode], l1_nodes: list[TaxonomyNode]) -> str:
        """构建完整的标签树文本（全层级递归），用于 LLM prompt"""
        lines: list[str] = []

        def _walk(node_id: str, depth: int):
            node = nodes_map.get(node_id)
            if not node:
                return
            indent = "  " * (depth - 1) if depth > 0 else ""
            lines.append(f"{indent}{node.id} | {node.name_zh}")
            for child_id in node.children_ids:
                _walk(child_id, depth + 1)

        for l1 in l1_nodes:
            _walk(l1.id, 0)
        return "\n".join(lines)

    def get_taxonomy_summary(self) -> dict[str, Any]:
        """获取标签体系概览"""
        self._ensure_loaded()
        return {
            "total_nodes": len(self._nodes),
            "l1_count": len(self._l1_nodes),
            "l2_count": len(self._l2_nodes),
            "l1_categories": [
                {"id": n.id, "name_zh": n.name_zh, "name_en": n.name_en, "children_count": len(n.children_ids)}
                for n in self._l1_nodes
            ],
        }

    def get_taxonomy_tree(self) -> list[dict]:
        """获取完整标签树"""
        self._ensure_loaded()
        return [
            {
                "id": n.id,
                "name_zh": n.name_zh,
                "name_en": n.name_en,
                "level": n.level,
                "parent_id": n.parent_id,
                "path": n.path,
                "source": n.source,
                "dimension": n.dimension,
            }
            for n in self._nodes
        ]

    def get_node_path(self, tag_id: str) -> str:
        """获取标签的完整路径"""
        self._ensure_loaded()
        node = self._nodes_map.get(tag_id)
        return node.path if node else ""

    def get_l2_display(self, tag_id: str) -> dict | None:
        """获取标签对应的 L2 层级展示信息"""
        self._ensure_loaded()
        node = self._nodes_map.get(tag_id)
        if not node:
            return None

        # Walk up to L2
        current = node
        while current and current.level > 2:
            current = self._nodes_map.get(current.parent_id) if current.parent_id else None

        if current and current.level == 2:
            parent = self._nodes_map.get(current.parent_id) if current.parent_id else None
            return {
                "l1_id": parent.id if parent else None,
                "l1_name": parent.name_zh if parent else None,
                "l2_id": current.id,
                "l2_name": current.name_zh,
            }

        # If node is L1 itself
        if node.level == 1:
            return {"l1_id": node.id, "l1_name": node.name_zh, "l2_id": None, "l2_name": None}

        return None

    def get_ancestor_path_display(self, tag_id: str) -> str:
        """获取标签的祖先路径显示文本（排除 L1），如 "L2名 > L3名 > 标签名" """
        self._ensure_loaded()
        node = self._nodes_map.get(tag_id)
        if not node:
            return ""

        # Collect ancestors from current node up to (but excluding) L1
        ancestors: list[str] = []
        current = node
        while current:
            if current.level >= 2:
                ancestors.append(current.name_zh)
            parent_id = current.parent_id
            current = self._nodes_map.get(parent_id) if parent_id else None

        # ancestors is [tag, L3, L2, ...] — reverse to get [L2, L3, ..., tag]
        ancestors.reverse()
        return " > ".join(ancestors) if len(ancestors) > 1 else (ancestors[0] if ancestors else "")

    async def auto_tag_text(
        self,
        content: str,
        model_spec: str | None = None,
        max_tags: int = 5,
        confidence_threshold: float = 0.65,
        source_type: str = "",
    ) -> list[dict]:
        """
        文本自动打标（混合模式：规则 + LLM）

        Args:
            content: 待打标的文本内容
            model_spec: 模型标识（如 'siliconflow/Qwen/Qwen3-8B'），为 None 时使用 fast_model
            max_tags: 最大标签数
            confidence_threshold: LLM 置信度阈值
            source_type: 内容来源类型（video/audio/text/image）

        Returns:
            标签结果列表
        """
        self._ensure_loaded()

        if not content or not content.strip():
            return []

        # 截断过长文本（视频/音频描述可能更长）
        max_len = 8000 if source_type in ("video", "audio", "music") else 4000
        content_truncated = content[:max_len] if len(content) > max_len else content

        # 视频/音频需要更多标签覆盖多维度
        if source_type in ("video", "audio", "music") and max_tags < 10:
            max_tags = 15

        # Step 1: 规则引擎
        rules_results = run_rules_engine(content_truncated, self._nodes_map)
        rules_ids = {r.tag_id for r in rules_results}

        # Step 2: LLM 打标
        llm_results = await self._llm_tag(content_truncated, model_spec, source_type=source_type)

        # Step 3: 合并去重（规则优先，LLM 补充）
        seen_ids = {r.tag_id for r in rules_results}
        seen_names = {r.tag_name for r in rules_results}
        merged: list[TagResult] = list(rules_results)
        for lr in llm_results:
            if lr.tag_id in seen_ids or lr.tag_name in seen_names:
                continue
            if lr.confidence >= confidence_threshold:
                merged.append(lr)
                seen_ids.add(lr.tag_id)
                seen_names.add(lr.tag_name)

        # 按置信度排序，截取 top-N
        merged.sort(key=lambda r: r.confidence, reverse=True)
        merged = merged[:max_tags]

        # 补充完整路径和层级展示信息
        results = []
        for r in merged:
            result = r.to_dict()
            if r.source == "llm_suggested":
                result["is_suggested"] = True
            result["l2_display"] = self.get_l2_display(r.tag_id)
            result["ancestor_path"] = self.get_ancestor_path_display(r.tag_id)
            results.append(result)

        return results

    async def _llm_tag(self, content: str, model_spec: str | None = None, source_type: str = "") -> list[TagResult]:
        """调用 LLM 进行打标"""
        from yuxi import config
        from yuxi.models import select_model
        from yuxi.services.tagging.prompt_config import get_prompt_config

        prompt_cfg = get_prompt_config()
        tag_model = prompt_cfg.get("models", {}).get("tag_model", "")
        model_spec = model_spec or tag_model or config.fast_model
        if not model_spec:
            logger.warning("No model_spec configured for tagging, skipping LLM tagging")
            return []

        try:
            model = select_model(model_spec=model_spec)
        except Exception as e:
            logger.error(f"Failed to load model for tagging: {e}")
            return []

        system_msg = prompt_cfg.get("prompts", {}).get("text_system_prompt", SYSTEM_PROMPT).replace(
            "{taxonomy_tree}", self._taxonomy_tree_text
        )

        # 根据来源类型选择用户提示词（支持通过 prompt_config 自定义）
        prompts = prompt_cfg.get("prompts", {})
        if source_type == "video":
            user_msg = (prompts.get("video_user_prompt") or VIDEO_USER_PROMPT).replace("{content}", content)
        elif source_type == "music":
            user_msg = (prompts.get("music_user_prompt") or MUSIC_USER_PROMPT).replace("{content}", content)
        elif source_type == "audio":
            user_msg = (prompts.get("audio_user_prompt") or AUDIO_USER_PROMPT).replace("{content}", content)
        else:
            user_msg = (prompts.get("text_user_prompt") or USER_PROMPT).replace("{content}", content)

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

        try:
            response = await self._call_llm_with_retry(model, messages)
            raw_text = response.content if hasattr(response, "content") else str(response)
            logger.debug(f"LLM raw response (first 500 chars): {raw_text[:500]}")
            return self._parse_llm_response(raw_text)
        except Exception as e:
            logger.error(f"LLM tagging failed: {e}", exc_info=True)
            return []

    @staticmethod
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    async def _call_llm_with_retry(model, messages: list[dict]):
        """带重试的 LLM 调用"""
        return await model.call(messages)

    def _parse_llm_response(self, raw_text: str) -> list[TagResult]:
        """解析 LLM 返回的 JSON 数组"""
        # Extract JSON from markdown code block if present
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw_text, re.DOTALL)
        json_text = json_match.group(1).strip() if json_match else raw_text.strip()

        try:
            tags_data = json.loads(json_text)
        except json.JSONDecodeError:
            # Try to find JSON array in the text
            arr_match = re.search(r"\[.*\]", json_text, re.DOTALL)
            if arr_match:
                try:
                    tags_data = json.loads(arr_match.group())
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse LLM tagging response: {raw_text[:200]}")
                    return []
            else:
                logger.warning(f"No JSON array found in LLM response: {raw_text[:200]}")
                return []

        if not isinstance(tags_data, list):
            return []

        results: list[TagResult] = []
        seen_ids: set[str] = set()  # LLM 响应内部去重
        for item in tags_data:
            if not isinstance(item, dict):
                continue
            tag_id = item.get("tag_id", "")
            tag_name = item.get("tag_name", "")
            confidence = float(item.get("confidence", 0.5))
            reasoning = item.get("reasoning", "")

            # 新标签建议（tag_id == "__new__"）
            if tag_id == "__new__" and tag_name:
                suggested_parent = item.get("suggested_parent", "")
                dedup_key = f"__new__{tag_name}"
                if dedup_key in seen_ids:
                    continue
                seen_ids.add(dedup_key)
                results.append(
                    TagResult(
                        tag_id=dedup_key,
                        tag_name=tag_name,
                        path=f"(建议新增) {tag_name}",
                        level=0,
                        confidence=confidence,
                        source="llm_suggested",
                        reasoning=f"{reasoning} [建议挂载: {suggested_parent}]" if suggested_parent else reasoning,
                    )
                )
                continue

            # Validate tag exists in taxonomy
            node = self._nodes_map.get(tag_id)
            if node:
                if tag_id in seen_ids:
                    continue
                seen_ids.add(tag_id)
                results.append(
                    TagResult(
                        tag_id=tag_id,
                        tag_name=node.name_zh,
                        path=node.path,
                        level=node.level,
                        confidence=confidence,
                        source="llm",
                        reasoning=reasoning,
                    )
                )
            else:
                # LLM might return slightly different ID, try fuzzy match by name
                matched = self._fuzzy_match_by_name(tag_name)
                if matched:
                    if matched.id in seen_ids:
                        continue
                    seen_ids.add(matched.id)
                    results.append(
                        TagResult(
                            tag_id=matched.id,
                            tag_name=matched.name_zh,
                            path=matched.path,
                            level=matched.level,
                            confidence=confidence * 0.9,  # Slightly reduce confidence for fuzzy match
                            source="llm",
                            reasoning=reasoning,
                        )
                    )

        return results

    def _fuzzy_match_by_name(self, name: str) -> TaxonomyNode | None:
        """通过标签名匹配（精确 O(1) → 包含匹配 O(n) 兜底）"""
        if not name:
            return None
        # O(1) 精确匹配
        exact = self._name_zh_map.get(name)
        if exact:
            return exact
        # 包含匹配兜底
        for node in self._nodes:
            if name in node.name_zh or node.name_zh in name:
                return node
        return None

    # 兼容旧调用
    async def auto_tag(self, content: str, **kwargs) -> list[dict]:
        return await self.auto_tag_text(content, **kwargs)
