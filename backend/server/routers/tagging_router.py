"""标签服务路由"""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel

from server.utils.auth_middleware import get_admin_user, get_required_user
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger

tagging = APIRouter(prefix="/tagging", tags=["tagging"])


# ============================================================
# Pydantic 请求模型
# ============================================================


class BatchTagRequest(BaseModel):
    file_ids: list[str]
    db_id: str


class TagUpdateRequest(BaseModel):
    tags: list[dict]


class NodeCreateRequest(BaseModel):
    name_zh: str
    parent_id: str | None = None
    name_en: str = ""
    source: str = "custom"
    dimension: str = ""


class NodeUpdateRequest(BaseModel):
    name_zh: str | None = None
    name_en: str | None = None


class NodeMoveRequest(BaseModel):
    new_parent_id: str | None = None


class SynonymsRequest(BaseModel):
    synonyms: list[str]


class TestPromptRequest(BaseModel):
    content: str


# ============================================================
# 标签体系
# ============================================================


@tagging.get("/taxonomy")
async def get_taxonomy(current_user: User = Depends(get_required_user)) -> dict[str, Any]:
    """获取标签体系概览"""
    from yuxi.services.tagging_service import TaggingService

    service = TaggingService()
    return service.get_taxonomy_summary()


@tagging.get("/taxonomy/tree")
async def get_taxonomy_tree(current_user: User = Depends(get_required_user)) -> list[dict]:
    """获取完整标签树"""
    from yuxi.services.tagging import taxonomy_manager

    return taxonomy_manager.get_tree()


@tagging.post("/taxonomy/nodes")
async def add_taxonomy_node(
    req: NodeCreateRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """添加标签节点"""
    from yuxi.services.tagging import taxonomy_manager

    try:
        return await taxonomy_manager.add_node(
            name_zh=req.name_zh,
            parent_id=req.parent_id,
            name_en=req.name_en,
            source=req.source,
            dimension=req.dimension,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@tagging.put("/taxonomy/nodes/{node_id}")
async def update_taxonomy_node(
    node_id: str,
    req: NodeUpdateRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """修改标签节点"""
    from yuxi.services.tagging import taxonomy_manager

    updates = req.model_dump(exclude_none=True)
    result = await taxonomy_manager.update_node(node_id, updates)
    if not result:
        raise HTTPException(status_code=404, detail="节点不存在")
    return result


@tagging.delete("/taxonomy/nodes/{node_id}")
async def delete_taxonomy_node(
    node_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """删除标签节点及其所有子节点"""
    from yuxi.services.tagging import taxonomy_manager

    result = await taxonomy_manager.archive_node(node_id)
    if not result:
        raise HTTPException(status_code=404, detail="节点不存在")
    return result


@tagging.put("/taxonomy/nodes/{node_id}/move")
async def move_taxonomy_node(
    node_id: str,
    req: NodeMoveRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """移动标签节点"""
    from yuxi.services.tagging import taxonomy_manager

    try:
        result = await taxonomy_manager.move_node(node_id, req.new_parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="节点不存在")
    return result


@tagging.put("/taxonomy/nodes/{node_id}/synonyms")
async def update_synonyms(
    node_id: str,
    req: SynonymsRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """更新同义词"""
    from yuxi.services.tagging import taxonomy_manager

    result = await taxonomy_manager.update_synonyms(node_id, req.synonyms)
    if not result:
        raise HTTPException(status_code=404, detail="节点不存在")
    return result


@tagging.post("/taxonomy/search")
async def search_taxonomy(
    query: str = Body(..., embed=True),
    include_archived: bool = Body(False, embed=True),
    current_user: User = Depends(get_required_user),
) -> list[dict]:
    """搜索标签节点"""
    from yuxi.services.tagging import taxonomy_manager

    return taxonomy_manager.search_nodes(query, include_archived)


@tagging.post("/taxonomy/import")
async def import_taxonomy(
    data: dict = Body(...),
    merge: bool = Body(True, embed=True),
    current_user: User = Depends(get_admin_user),
) -> dict:
    """导入标签体系"""
    from yuxi.services.tagging import taxonomy_manager

    return await taxonomy_manager.import_taxonomy(data, merge)


@tagging.get("/taxonomy/export")
async def export_taxonomy(
    current_user: User = Depends(get_admin_user),
) -> dict:
    """导出标签体系"""
    from yuxi.services.tagging import taxonomy_manager

    return taxonomy_manager.export_taxonomy()


# ============================================================
# 打标任务
# ============================================================


@tagging.post("/auto-tag")
async def auto_tag_content(
    content: str = Body(..., embed=True),
    model_spec: str | None = Body(None, embed=True),
    max_tags: int = Body(5, embed=True),
    current_user: User = Depends(get_required_user),
) -> list[dict]:
    """对文本内容自动打标"""
    from yuxi.services.tagging_service import TaggingService

    if not content or not content.strip():
        raise HTTPException(status_code=400, detail="内容不能为空")

    service = TaggingService()
    return await service.auto_tag_text(content, model_spec=model_spec, max_tags=max_tags)


@tagging.post("/auto-tag-file/{file_id}")
async def auto_tag_file(
    file_id: str,
    db_id: str = Body(..., embed=True),
    model_spec: str | None = Body(None, embed=True),
    current_user: User = Depends(get_required_user),
) -> list[dict]:
    """对知识库文件自动打标"""
    from yuxi.knowledge import knowledge_base
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
    from yuxi.services.tagging_service import TaggingService

    repo = KnowledgeFileRepository()
    record = await repo.get_by_file_id(file_id)
    if record is None or record.kb_id != db_id:
        raise HTTPException(status_code=404, detail=f"文件 {file_id} 不存在")

    if not record.markdown_file:
        raise HTTPException(status_code=400, detail="文件尚未解析，请先解析文件")

    try:
        kb_instance = await knowledge_base._get_kb_for_database(db_id)
        markdown_content = await kb_instance._read_markdown_from_minio(record.markdown_file)
    except Exception as e:
        logger.error(f"Failed to read markdown for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"读取文件内容失败: {e}")

    service = TaggingService()
    tags = await service.auto_tag_text(markdown_content, model_spec=model_spec)

    params = dict(record.processing_params or {})
    params["tags"] = tags
    await repo.update_fields(file_id=file_id, data={"processing_params": params})

    return tags


@tagging.post("/batch-tag")
async def batch_tag(
    req: BatchTagRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """批量创建打标任务"""
    from yuxi.knowledge import knowledge_base
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
    from yuxi.services.tagging import task_manager

    db_info = await knowledge_base.get_database_info(req.db_id)
    if db_info is None:
        raise HTTPException(status_code=404, detail=f"知识库 {req.db_id} 不存在")

    repo = KnowledgeFileRepository()
    records = await repo.list_by_file_ids(req.file_ids)

    tasks = []
    for record in records:
        if record.kb_id != req.db_id or record.is_folder:
            continue
        mime_type = record.content_type or ""
        if not mime_type or "/" not in mime_type:
            import mimetypes as _mt

            mime_type, _ = _mt.guess_type(record.filename or "")
            mime_type = mime_type or ""
        task = task_manager.create_task(
            file_id=record.file_id,
            db_id=req.db_id,
            filename=record.filename or record.file_id,
            file_type=record.file_type or "unknown",
            mime_type=mime_type,
        )
        tasks.append(task)

    if not tasks:
        raise HTTPException(status_code=400, detail="没有有效的文件")

    task_ids = await task_manager.add_tasks(tasks)

    # 注册到任务中心
    from yuxi.services.task_service import TaskContext, tasker

    async def _tagging_coroutine(ctx: TaskContext):
        total = len(task_ids)
        for i, tid in enumerate(task_ids):
            if ctx.is_cancel_requested():
                await ctx.set_message("已取消")
                return
            await ctx.set_progress((i / total) * 100, f"正在打标 {i + 1}/{total}")
            await task_manager.run_task(tid)
        await ctx.set_progress(100, f"已完成 {total} 个文件的打标")
        await ctx.set_result({"task_ids": task_ids})

    db_name = (db_info or {}).get("name", req.db_id)
    await tasker.enqueue(
        name=f"自动打标 ({db_name})",
        task_type="tagging",
        payload={"db_id": req.db_id, "count": len(task_ids)},
        coroutine=_tagging_coroutine,
    )

    return {"task_ids": task_ids, "count": len(task_ids)}


@tagging.get("/tasks")
async def get_tasks(
    status: str | None = Query(None),
    db_id: str | None = Query(None),
    file_type: str | None = Query(None),
    sort_by: str = Query("created_at"),
    ascending: bool = Query(True),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
) -> dict:
    """获取打标任务列表"""
    from yuxi.services.tagging import task_manager

    return task_manager.list_tasks(
        status=status,
        db_id=db_id,
        file_type=file_type,
        sort_by=sort_by,
        ascending=ascending,
        page=page,
        page_size=page_size,
    )


@tagging.post("/tasks/{task_id}/approve")
async def approve_task(
    task_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """审核通过"""
    from yuxi.services.tagging import task_manager

    result = await task_manager.approve_task(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="任务不存在")
    return result


@tagging.post("/tasks/{task_id}/reject")
async def reject_task(
    task_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """拒绝任务"""
    from yuxi.services.tagging import task_manager

    result = await task_manager.reject_task(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="任务不存在")
    return result


@tagging.put("/tasks/{task_id}/tags")
async def update_task_tags(
    task_id: str,
    req: TagUpdateRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """修改标签后通过"""
    from yuxi.services.tagging import task_manager

    result = await task_manager.approve_task(task_id, tags=req.tags)
    if not result:
        raise HTTPException(status_code=404, detail="任务不存在")
    return result


@tagging.post("/tasks/{task_id}/retry")
async def retry_task(
    task_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """重试失败或已拒绝的任务"""
    from yuxi.services.tagging import task_manager

    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task["status"] not in ("error_preprocessing", "error_tagging", "rejected"):
        raise HTTPException(status_code=400, detail="只能重试失败或已拒绝的任务")

    # 重置状态为 pending
    await task_manager.update_task(task_id, {
        "status": "pending",
        "tags": [],
        "avg_confidence": 0.0,
        "error": None,
    })

    # 异步执行
    from yuxi.services.task_service import TaskContext, tasker

    async def _retry_coroutine(ctx: TaskContext):
        await ctx.set_progress(0, "正在重试打标...")
        await task_manager.run_task(task_id)
        await ctx.set_progress(100, "重试完成")

    await tasker.enqueue(
        name=f"重试打标 ({task['filename']})",
        task_type="tagging",
        payload={"task_id": task_id},
        coroutine=_retry_coroutine,
    )

    return {"task_id": task_id, "status": "pending"}


class BatchTaskIdsRequest(BaseModel):
    task_ids: list[str]


@tagging.post("/tasks/batch-retry")
async def batch_retry_tasks(
    req: BatchTaskIdsRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """批量重试失败/已拒绝任务"""
    from yuxi.services.tagging import task_manager

    retryable = ("error_preprocessing", "error_tagging", "rejected")
    retry_ids = []
    for tid in req.task_ids:
        task = await task_manager.get_task(tid)
        if task and task["status"] in retryable:
            await task_manager.update_task(tid, {
                "status": "pending",
                "tags": [],
                "avg_confidence": 0.0,
                "error": None,
            })
            retry_ids.append(tid)

    if not retry_ids:
        raise HTTPException(status_code=400, detail="没有可重试的任务")

    from yuxi.services.task_service import TaskContext, tasker

    async def _batch_retry(ctx: TaskContext):
        total = len(retry_ids)
        for i, tid in enumerate(retry_ids):
            if ctx.is_cancel_requested():
                return
            await ctx.set_progress((i / total) * 100, f"正在重试 {i + 1}/{total}")
            await task_manager.run_task(tid)
        await ctx.set_progress(100, f"已重试 {total} 个任务")

    await tasker.enqueue(
        name=f"批量重试打标 ({len(retry_ids)}个)",
        task_type="tagging",
        payload={"count": len(retry_ids)},
        coroutine=_batch_retry,
    )

    return {"retried": len(retry_ids), "task_ids": retry_ids}


@tagging.post("/tasks/batch-approve")
async def batch_approve_tasks(
    req: BatchTaskIdsRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """批量通过审核"""
    from yuxi.services.tagging import task_manager

    approved = 0
    for tid in req.task_ids:
        result = await task_manager.approve_task(tid)
        if result:
            approved += 1

    return {"approved": approved}


@tagging.post("/tasks/batch-delete")
async def batch_delete_tasks(
    req: BatchTaskIdsRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """批量删除任务"""
    from yuxi.services.tagging import task_manager

    deleted = await task_manager.delete_tasks(req.task_ids)
    return {"deleted": deleted}


@tagging.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """删除单个任务"""
    from yuxi.services.tagging import task_manager

    deleted = await task_manager.delete_tasks([task_id])
    if deleted == 0:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"deleted": 1}


# ============================================================
# 配置
# ============================================================


@tagging.get("/prompt-config")
async def get_config(
    current_user: User = Depends(get_admin_user),
) -> dict:
    """获取打标配置"""
    from yuxi.services.tagging.prompt_config import get_prompt_config

    return get_prompt_config()


@tagging.put("/prompt-config")
async def update_config(
    config: dict = Body(...),
    current_user: User = Depends(get_admin_user),
) -> dict:
    """更新打标配置"""
    from yuxi.services.tagging.pipeline import get_semaphore
    from yuxi.services.tagging.prompt_config import save_prompt_config

    await save_prompt_config(config)

    # 动态更新并发限制
    new_limit = config.get("processing", {}).get("max_concurrent_tasks")
    if new_limit is not None:
        get_semaphore().update_limit(new_limit)

    return {"status": "ok"}


@tagging.post("/test-prompt")
async def test_prompt(
    req: TestPromptRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """测试提示词效果"""
    from yuxi.services.tagging_service import TaggingService

    service = TaggingService()
    tags = await service.auto_tag_text(req.content)
    return {"tags": tags}


# ============================================================
# 统计
# ============================================================


@tagging.get("/stats")
async def get_stats(
    current_user: User = Depends(get_required_user),
) -> dict:
    """获取打标统计"""
    from yuxi.services.tagging import task_manager

    return task_manager.get_stats()


@tagging.get("/stats/concurrency")
async def get_concurrency(
    current_user: User = Depends(get_required_user),
) -> dict:
    """获取当前并发信息"""
    from yuxi.services.tagging.pipeline import get_semaphore

    sem = get_semaphore()
    return {"current": sem.current, "limit": sem.limit}


@tagging.post("/tasks/archive")
async def archive_tasks(
    days: int = Body(30, embed=True),
    current_user: User = Depends(get_admin_user),
) -> dict:
    """归档超过指定天数的已完成任务"""
    from yuxi.services.tagging import task_manager

    archived = await task_manager.archive_completed_tasks(days)
    return {"archived": archived}


@tagging.post("/upload-and-tag")
async def upload_and_tag(
    file: UploadFile,
    db_id: str | None = Query(None),
    current_user: User = Depends(get_admin_user),
) -> dict:
    """上传文件并创建打标任务（无需关联知识库）"""
    import mimetypes as _mt

    from yuxi.services.tagging import task_manager

    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    file_data = await file.read()
    if not file_data:
        raise HTTPException(status_code=400, detail="文件内容为空")

    # 推断 MIME 类型
    mime_type = file.content_type or ""
    if not mime_type or "/" not in mime_type:
        mime_type, _ = _mt.guess_type(file.filename)
        mime_type = mime_type or ""

    file_type = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "unknown"

    # 创建任务（file_id 用 upload_ 前缀区别于真实知识库文件）
    import hashlib

    file_hash = hashlib.md5(file_data).hexdigest()[:8]
    fake_file_id = f"upload_{file_hash}"

    task = task_manager.create_task(
        file_id=fake_file_id,
        db_id=db_id or "__standalone__",
        filename=file.filename,
        file_type=file_type,
        mime_type=mime_type,
    )
    await task_manager.add_tasks([task])
    task_id = task["task_id"]

    # 异步执行打标
    from yuxi.services.task_service import TaskContext, tasker

    async def _upload_tag_coroutine(ctx: TaskContext):
        from yuxi.services.tagging.pipeline import TaggingPipeline

        await ctx.set_progress(10, "预处理中...")
        await task_manager.update_task(task_id, {"status": "preprocessing"})

        pipeline = TaggingPipeline()
        # 文本类尝试直接解码
        markdown_content = None
        if file_type in ("txt", "md", "csv"):
            markdown_content = file_data.decode("utf-8", errors="replace")

        await task_manager.update_task(task_id, {"status": "tagging"})
        await ctx.set_progress(30, "打标中...")

        result = await pipeline.process_file(
            file_data=file_data,
            filename=file.filename,
            mime_type=mime_type,
            markdown_content=markdown_content,
        )

        tags = result.get("tags", [])
        avg_conf = result.get("avg_confidence", 0)

        if task_manager.should_auto_approve(tags, avg_conf):
            await task_manager.update_task(task_id, {
                "status": "approved",
                "tags": tags,
                "avg_confidence": avg_conf,
            })
        else:
            await task_manager.update_task(task_id, {
                "status": "review",
                "tags": tags,
                "avg_confidence": avg_conf,
            })

        await ctx.set_progress(100, f"完成，{len(tags)} 个标签")
        await ctx.set_result({"task_id": task_id, "tags_count": len(tags)})

    await tasker.enqueue(
        name=f"上传打标 ({file.filename})",
        task_type="tagging",
        payload={"filename": file.filename, "task_id": task_id},
        coroutine=_upload_tag_coroutine,
    )

    return {"task_id": task_id, "filename": file.filename, "status": "pending"}
