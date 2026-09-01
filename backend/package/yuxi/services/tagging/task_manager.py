"""
打标任务队列管理

持久化任务到 saves/tasks/tagging_tasks.json
6+2 状态机: pending → preprocessing → preprocessed → tagging → tagged → review → approved/rejected
                        ↓                                 ↓
                  error_preprocessing                error_tagging
"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from yuxi.utils import logger

_TASKS_DIR = Path(os.getenv("SAVE_DIR", "saves")) / "tasks"
_TASKS_FILE = _TASKS_DIR / "tagging_tasks.json"
_lock = asyncio.Lock()

VALID_STATES = {
    "pending",
    "preprocessing",
    "preprocessed",
    "tagging",
    "tagged",
    "review",
    "approved",
    "rejected",
    "error_preprocessing",
    "error_tagging",
}

# 终态：不再变化的状态
_TERMINAL_STATES = {"approved", "rejected"}

# 归档保留天数
_ARCHIVE_DAYS = 30


def _ensure_dir():
    _TASKS_DIR.mkdir(parents=True, exist_ok=True)


def _load_tasks_sync() -> dict[str, Any]:
    """读取所有任务（同步，调用方需持有 _lock 或为只读场景）"""
    if _TASKS_FILE.exists():
        try:
            with open(_TASKS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load tagging tasks: {e}")
    return {"tasks": {}}


def _save_tasks_sync(data: dict) -> None:
    """原子写入任务文件（同步，调用方需持有 _lock）"""
    _ensure_dir()
    tmp = _TASKS_FILE.with_suffix(".tmp")
    content = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(_TASKS_FILE)


def create_task(
    file_id: str,
    db_id: str,
    filename: str,
    file_type: str,
    mime_type: str = "",
) -> dict:
    """创建打标任务（同步，用于批量创建）"""
    task_id = str(uuid.uuid4())[:8]
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    task = {
        "task_id": task_id,
        "file_id": file_id,
        "db_id": db_id,
        "filename": filename,
        "file_type": file_type,
        "mime_type": mime_type,
        "status": "pending",
        "tags": [],
        "avg_confidence": 0.0,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    return task


async def add_tasks(tasks: list[dict]) -> list[str]:
    """批量添加任务（原子操作）"""
    async with _lock:
        data = _load_tasks_sync()
        task_ids = []
        for t in tasks:
            data["tasks"][t["task_id"]] = t
            task_ids.append(t["task_id"])
        _save_tasks_sync(data)
    return task_ids


async def update_task(task_id: str, updates: dict) -> dict | None:
    """更新任务字段（原子读-改-写）"""
    async with _lock:
        data = _load_tasks_sync()
        task = data["tasks"].get(task_id)
        if not task:
            return None
        task.update(updates)
        task["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        data["tasks"][task_id] = task
        _save_tasks_sync(data)
        return task


async def get_task(task_id: str) -> dict | None:
    """获取单个任务（只读，无需锁）"""
    data = _load_tasks_sync()
    return data["tasks"].get(task_id)


def list_tasks(
    status: str | None = None,
    db_id: str | None = None,
    file_type: str | None = None,
    sort_by: str = "created_at",
    ascending: bool = True,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """列出任务（带筛选和分页）"""
    data = _load_tasks_sync()
    tasks = list(data["tasks"].values())

    # 筛选
    if status:
        tasks = [t for t in tasks if t["status"] == status]
    if db_id:
        tasks = [t for t in tasks if t["db_id"] == db_id]
    if file_type:
        tasks = [t for t in tasks if t["file_type"] == file_type]

    # 排序
    if sort_by == "confidence":
        tasks.sort(key=lambda t: t.get("avg_confidence", 0), reverse=not ascending)
    else:
        tasks.sort(key=lambda t: t.get(sort_by, ""), reverse=not ascending)

    # 分页
    total = len(tasks)
    start = (page - 1) * page_size
    end = start + page_size
    page_tasks = tasks[start:end]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "tasks": page_tasks,
    }


async def approve_task(task_id: str, tags: list[dict] | None = None) -> dict | None:
    """审核通过（可修改标签，原子操作）"""
    async with _lock:
        data = _load_tasks_sync()
        task = data["tasks"].get(task_id)
        if not task:
            return None
        if tags is not None:
            task["tags"] = tags
        task["status"] = "approved"
        task["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        data["tasks"][task_id] = task
        _save_tasks_sync(data)

    # 同步标签到知识库文件记录（在锁外执行，避免长时间持锁）
    await _sync_tags_to_kb(task)
    return task


async def reject_task(task_id: str) -> dict | None:
    """拒绝任务"""
    return await update_task(task_id, {"status": "rejected"})


async def _sync_tags_to_kb(task: dict) -> None:
    """将审核通过的标签同步到知识库文件记录（存入 processing_params.tags）"""
    try:
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        repo = KnowledgeFileRepository()
        file_id = task["file_id"]
        record = await repo.get_by_file_id(file_id)
        if record is None:
            return
        params = dict(record.processing_params or {})
        params["tags"] = task["tags"]
        await repo.update_fields(file_id=file_id, data={"processing_params": params})
        logger.info(f"Synced tags to kb for file {file_id}")
    except Exception as e:
        logger.warning(f"Failed to sync tags to kb: {e}")


async def run_task(task_id: str) -> dict | None:
    """执行单个打标任务的完整流程"""
    task = await get_task(task_id)
    if not task:
        return None

    from yuxi.knowledge import knowledge_base
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
    from yuxi.services.tagging.pipeline import TaggingPipeline
    from yuxi.services.tagging.prompt_config import get_prompt_config
    from yuxi.storage.minio import get_minio_client

    db_id = task["db_id"]
    file_id = task["file_id"]
    cfg = get_prompt_config()
    task_timeout = cfg.get("processing", {}).get("task_timeout_seconds", 600)

    try:
        repo = KnowledgeFileRepository()
        record = await repo.get_by_file_id(file_id)
        if record is None:
            await update_task(task_id, {"status": "error_preprocessing", "error": "文件不存在"})
            return await get_task(task_id)

        file_params = dict(record.processing_params or {})

        # 检查是否有缓存的预处理结果（必须包含 text 才算有效）
        preprocessed_cache = file_params.get("preprocessed")
        if isinstance(preprocessed_cache, dict) and not preprocessed_cache.get("text"):
            preprocessed_cache = None  # 缓存无效，需重新预处理

        # Step 1: preprocessing
        await update_task(task_id, {"status": "preprocessing"})

        import mimetypes as _mt

        mime_type = task.get("mime_type", "") or (record.content_type or "")
        # 无效的 MIME（如 "NONE"、空值、不含 "/" 的字符串）→ 从文件名推断
        if not mime_type or "/" not in mime_type:
            mime_type, _ = _mt.guess_type(task.get("filename", ""))
            mime_type = mime_type or ""
        file_data = b""
        markdown_content = None

        if not preprocessed_cache:
            # 需要预处理：下载原始文件（MinIO URL 存于 minio_url/path 字段）
            file_path = record.minio_url or record.path or record.markdown_file
            if file_path and file_path.startswith(("http://", "https://")):
                from yuxi.knowledge.utils.kb_utils import parse_minio_url

                minio_client = get_minio_client()
                bucket_name, object_name = parse_minio_url(file_path)
                file_data = await minio_client.adownload_file(bucket_name, object_name)

            # 尝试读取已解析的 markdown
            if record.markdown_file:
                try:
                    kb_instance = await knowledge_base._get_kb_for_database(db_id)
                    markdown_content = await kb_instance._read_markdown_from_minio(record.markdown_file)
                except Exception:
                    pass

            # 已解析的文件：优先使用 markdown 直接打标，跳过昂贵的多模态预处理
            if markdown_content and markdown_content.strip():
                logger.info(f"Using existing markdown for {task['filename']}, skip preprocessing")
                file_data = b""  # 无需再下载原始文件
                mime_type = "text/markdown"  # 强制文本路径

        await update_task(task_id, {"status": "preprocessed"})

        # Step 2: tagging
        await update_task(task_id, {"status": "tagging"})

        pipeline = TaggingPipeline()
        result = await asyncio.wait_for(
            pipeline.process_file(
                file_data=file_data,
                filename=task["filename"],
                mime_type=mime_type,
                markdown_content=markdown_content,
                preprocessed_cache=preprocessed_cache,
            ),
            timeout=task_timeout,
        )

        # 保存预处理结果到文件记录（processing_params.preprocessed）
        if result.get("preprocessed") and not preprocessed_cache:
            file_params["preprocessed"] = result["preprocessed"]
            await repo.update_fields(file_id=file_id, data={"processing_params": file_params})

        await update_task(task_id, {"status": "tagged"})

        # Step 3: 判断是否自动审批
        tags = result.get("tags", [])
        avg_conf = result.get("avg_confidence", 0)

        if should_auto_approve(tags, avg_conf):
            await update_task(task_id, {
                "status": "approved",
                "tags": tags,
                "avg_confidence": avg_conf,
            })
            task = await get_task(task_id)
            await _sync_tags_to_kb(task)
        else:
            await update_task(task_id, {
                "status": "review",
                "tags": tags,
                "avg_confidence": avg_conf,
            })

        return await get_task(task_id)

    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}", exc_info=True)
        current = await get_task(task_id)
        error_status = "error_tagging"
        if current and current.get("status") in ("pending", "preprocessing"):
            error_status = "error_preprocessing"
        await update_task(task_id, {"status": error_status, "error": str(e)})
        return await get_task(task_id)


async def delete_tasks(task_ids: list[str]) -> int:
    """删除指定任务（原子操作）"""
    async with _lock:
        data = _load_tasks_sync()
        deleted = 0
        for tid in task_ids:
            if tid in data["tasks"]:
                del data["tasks"][tid]
                deleted += 1
        if deleted:
            _save_tasks_sync(data)
    return deleted


def get_stats() -> dict:
    """获取打标统计"""
    data = _load_tasks_sync()
    tasks = list(data["tasks"].values())
    total = len(tasks)
    by_status: dict[str, int] = {}
    for t in tasks:
        s = t.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "total": total,
        "by_status": by_status,
        "pending_review": by_status.get("review", 0),
        "approved_today": sum(
            1
            for t in tasks
            if t.get("status") == "approved"
            and t.get("updated_at", "").startswith(time.strftime("%Y-%m-%d"))
        ),
    }


def should_auto_approve(tags: list[dict], avg_confidence: float, review_cfg: dict | None = None) -> bool:
    """统一的自动审批判断逻辑"""
    if review_cfg is None:
        from yuxi.services.tagging.prompt_config import get_prompt_config

        review_cfg = get_prompt_config().get("review", {})

    threshold = review_cfg.get("auto_approve_threshold", 0.85)
    require_rule_hit = review_cfg.get("auto_approve_require_rule_hit", True)
    has_rule_hit = any(t.get("source") == "rules" for t in tags)
    return avg_confidence >= threshold and (not require_rule_hit or has_rule_hit)


async def archive_completed_tasks(days: int = _ARCHIVE_DAYS) -> int:
    """归档超过指定天数的终态任务，返回归档数量"""
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400))
    async with _lock:
        data = _load_tasks_sync()
        to_archive = [
            tid for tid, t in data["tasks"].items()
            if t.get("status") in _TERMINAL_STATES and t.get("updated_at", "") < cutoff
        ]
        if not to_archive:
            return 0
        for tid in to_archive:
            del data["tasks"][tid]
        _save_tasks_sync(data)
    logger.info(f"Archived {len(to_archive)} completed tagging tasks older than {days} days")
    return len(to_archive)
