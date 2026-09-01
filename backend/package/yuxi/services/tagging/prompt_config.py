"""
提示词 + 运行配置管理

从 saves/config/tagging_prompts.json 加载配置，
提供运行时读写接口。
"""

import asyncio
import json
import os
from pathlib import Path

from yuxi.utils import logger

_CONFIG_DIR = Path(os.getenv("SAVE_DIR", "saves")) / "config"
_CONFIG_FILE = _CONFIG_DIR / "tagging_prompts.json"
_lock = asyncio.Lock()

_DEFAULT_CONFIG = {
    "prompts": {
        "text_system_prompt": (
            "你是一个专业的新闻/媒体内容分类标注专家。你的任务是根据给定的标签体系，为输入的文本内容分配最精准的标签。\n\n"
            "## 标签体系\n\n"
            "以下是完整的标签分类树（缩进表示层级关系，格式：ID | 标签名）：\n{taxonomy_tree}\n\n"
            "## 核心标注原则\n\n"
            "1. **深度优先**：必须打到标签体系中最深、最具体的层级。优先选择叶子节点。\n"
            "2. **多维覆盖**：从内容主题、类型形式、情感基调、受众类型、行业领域等多维度标注\n"
            "3. **标签数量**：选择 3~10 个标签\n"
            "4. **允许建议新标签**：内容涉及现有体系未覆盖的概念时，可建议新标签（tag_id 为 __new__）\n"
            "5. **置信度标准**：>0.9 非常确定, 0.7~0.9 比较确定, 0.5~0.7 可能相关\n\n"
            "## 输出格式\n\n"
            '严格输出 JSON 数组：\n```json\n[\n  {{"tag_id": "标签ID", "tag_name": "标签名", "confidence": 0.95, "reasoning": "理由"}},\n  {{"tag_id": "__new__", "tag_name": "新标签名", "confidence": 0.80, "reasoning": "理由", "suggested_parent": "父标签ID"}},\n  ...\n]\n```'
        ),
        "text_user_prompt": "请为以下内容进行深度标注（打到最深层级，允许建议新标签）：\n\n---\n{content}\n---\n\n请输出 JSON 数组：",
        "image_describe_prompt": "请详细描述这张图片的主要内容、场景、人物和关键信息。",
        "audio_transcribe_prompt": "请描述这段音频的主要内容，包括主题、关键信息和情感基调。",
        "video_summary_prompt": "根据以下视频的画面描述和音频内容，总结视频的主题、关键信息和重要细节。",
        "video_user_prompt": "",
        "audio_user_prompt": "",
        "music_user_prompt": "",
    },
    "models": {
        # v0.7.1 起 model spec 为 "provider_id:model_id" 格式（旧 "dashscope/x" 斜杠格式已废弃）
        "vl_model": "alibaba:qwen3.7-plus",
        "audio_model": "alibaba:qwen3.5-omni-plus",
        "asr_model": "qwen3-asr-flash-filetrans",
        "tag_model": "",
    },
    "processing": {
        "max_concurrent_tasks": 3,
        "audio_strategy_threshold_minutes": 30,
        "video_frame_interval_seconds": 30,
        "video_max_frames": 10,
        "long_audio_summary_segments": 10,
        "task_timeout_seconds": 600,
    },
    "review": {
        "max_tags": 10,
        "confidence_threshold": 0.5,
        "auto_approve_threshold": 0.85,
        "auto_approve_require_rule_hit": True,
        "auto_tag_on_parse": True,
    },
}


def _ensure_config_dir():
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def get_prompt_config() -> dict:
    """读取配置（同步），不存在则返回默认"""
    if _CONFIG_FILE.exists():
        try:
            with open(_CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            # 合并默认值（处理新增字段）
            merged = _deep_merge(_DEFAULT_CONFIG, data)
            return merged
        except Exception as e:
            logger.warning(f"Failed to load tagging config, using defaults: {e}")
    return _DEFAULT_CONFIG.copy()


async def save_prompt_config(config: dict) -> None:
    """保存配置（异步，原子写入）"""
    _ensure_config_dir()
    async with _lock:
        tmp_path = _CONFIG_FILE.with_suffix(".tmp")
        content = json.dumps(config, ensure_ascii=False, indent=2)
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(_CONFIG_FILE)
    logger.info("Tagging config saved")


def get_flat_processing_config() -> dict:
    """获取扁平化的处理配置（给预处理器用）"""
    cfg = get_prompt_config()
    result = {}
    result.update(cfg.get("processing", {}))
    result.update(cfg.get("models", {}))
    for k, v in cfg.get("prompts", {}).items():
        result[k] = v
    return result


def _deep_merge(default: dict, override: dict) -> dict:
    """深度合并，override 覆盖 default"""
    result = default.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
