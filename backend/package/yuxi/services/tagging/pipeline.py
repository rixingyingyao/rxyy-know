"""
打标管道调度器

根据文件 MIME type 自动路由到对应预处理器，
然后调用 TaggingService 进行文本打标。
包含 DynamicSemaphore 控制并发。
"""

import asyncio

from yuxi.utils import logger


class DynamicSemaphore:
    """支持运行时动态调整并发限制的信号量"""

    def __init__(self, limit: int = 3):
        self._limit = limit
        self._current = 0
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)

    async def acquire(self):
        async with self._condition:
            while self._current >= self._limit:
                await self._condition.wait()
            self._current += 1

    async def release(self):
        async with self._condition:
            self._current -= 1
            self._condition.notify()

    def update_limit(self, new_limit: int):
        """动态调整并发限制"""
        self._limit = max(1, new_limit)

    @property
    def current(self) -> int:
        return self._current

    @property
    def limit(self) -> int:
        return self._limit

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        await self.release()


# 全局信号量实例
_semaphore: DynamicSemaphore | None = None


def get_semaphore() -> DynamicSemaphore:
    global _semaphore
    if _semaphore is None:
        from yuxi.services.tagging.prompt_config import get_prompt_config

        cfg = get_prompt_config()
        limit = cfg.get("processing", {}).get("max_concurrent_tasks", 3)
        _semaphore = DynamicSemaphore(limit)
    return _semaphore


class TaggingPipeline:
    """打标管道：预处理 → 文本打标"""

    async def process_file(
        self,
        file_data: bytes,
        filename: str,
        mime_type: str,
        *,
        markdown_content: str | None = None,
        preprocessed_cache: dict | None = None,
    ) -> dict:
        """
        处理单个文件的完整打标流程

        Args:
            file_data: 文件二进制数据
            filename: 文件名
            mime_type: MIME 类型
            markdown_content: 已解析的 markdown 内容（文本文件可直接传入）
            preprocessed_cache: 缓存的预处理结果（跳过预处理）

        Returns:
            dict with keys: tags, preprocessed, confidence
        """
        from yuxi.services.tagging.preprocessors import (
            AudioPreprocessor,
            ImagePreprocessor,
            PreprocessResult,
            TextPreprocessor,
            VideoPreprocessor,
            detect_preprocessor_type,
        )
        from yuxi.services.tagging.prompt_config import get_flat_processing_config, get_prompt_config

        sem = get_semaphore()

        async with sem:
            cfg = get_flat_processing_config()
            review_cfg = get_prompt_config().get("review", {})

            # 有缓存则跳过预处理
            if preprocessed_cache and preprocessed_cache.get("text"):
                preprocess_result = PreprocessResult(
                    text=preprocessed_cache["text"],
                    source_type=preprocessed_cache.get("source_type", "text"),
                    model_used=preprocessed_cache.get("model_used", "cached"),
                )
                logger.info(f"Using cached preprocess result for {filename}")
            else:
                # 预处理
                ptype = detect_preprocessor_type(mime_type)

                if ptype == "audio":
                    preprocessor = AudioPreprocessor(cfg)
                    preprocess_result = await preprocessor.process(file_data, filename)
                elif ptype == "image":
                    preprocessor = ImagePreprocessor(cfg)
                    preprocess_result = await preprocessor.process(file_data, filename)
                elif ptype == "video":
                    preprocessor = VideoPreprocessor(cfg)
                    preprocess_result = await preprocessor.process(file_data, filename)
                else:
                    # 文本类：优先使用 markdown_content
                    preprocessor = TextPreprocessor()
                    text = markdown_content or (file_data.decode("utf-8", errors="replace") if file_data else "")
                    preprocess_result = await preprocessor.process(text, filename)

            # 打标
            from yuxi.services.tagging_service import TaggingService

            service = TaggingService()
            max_tags = review_cfg.get("max_tags", 5)
            threshold = review_cfg.get("confidence_threshold", 0.5)
            tags = await service.auto_tag_text(
                preprocess_result.text,
                max_tags=max_tags,
                confidence_threshold=threshold,
                source_type=preprocess_result.source_type,
            )

            # 计算平均置信度
            avg_confidence = 0.0
            if tags:
                avg_confidence = sum(t.get("confidence", 0) for t in tags) / len(tags)

            return {
                "tags": tags,
                "preprocessed": {
                    "text": preprocess_result.text[:10000],  # 截断存储
                    "source_type": preprocess_result.source_type,
                    "model_used": preprocess_result.model_used,
                },
                "avg_confidence": round(avg_confidence, 3),
            }
