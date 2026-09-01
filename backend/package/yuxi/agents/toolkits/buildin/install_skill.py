import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated

from langchain.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.logging_config import logger

SANDBOX_PATH_HINT = (
    "请使用 /home/gem/user-data/workspace/...、/home/gem/user-data/uploads/... 或 /home/gem/user-data/outputs/..."
)
MAX_SANDBOX_SKILL_FILES = 1000


class InstallSkillInput(BaseModel):
    source: str = Field(
        description="Skill 来源，支持两种格式:\n"
        "1. Sandbox 路径: /home/gem/user-data/workspace/...、"
        "/home/gem/user-data/uploads/... 或 /home/gem/user-data/outputs/...（/ 开头）\n"
        "2. Git 仓库: owner/repo 或完整 GitHub URL"
    )
    skill_names: list[str] | None = Field(
        default=None, description="Git 安装时指定要安装的 skill slug 列表（至少一个）。Sandbox 路径安装时忽略此参数。"
    )


def _personal_skill_share_config(uid: str) -> dict:
    return {"access_level": "user", "department_ids": [], "user_uids": [str(uid)]}


def _collect_sandbox_file_paths(backend, remote_dir: str, file_paths: list[str] | None = None) -> list[str]:
    file_paths = file_paths if file_paths is not None else []
    result = backend.ls(remote_dir)
    if result.error:
        raise ValueError(result.error)
    entries = result.entries or []
    for entry in entries:
        path = entry["path"]
        if entry.get("is_dir"):
            _collect_sandbox_file_paths(backend, path, file_paths)
        else:
            if len(file_paths) >= MAX_SANDBOX_SKILL_FILES:
                raise ValueError(f"Skill 目录文件数超过限制（最多 {MAX_SANDBOX_SKILL_FILES} 个文件）")
            file_paths.append(path)
    return file_paths


def _download_skill_dir(backend, remote_dir: str, local_dir: Path) -> None:
    """递归通过沙盒 API 下载 skill 目录到本地。"""
    remote_root = PurePosixPath(remote_dir.rstrip("/"))
    file_paths = _collect_sandbox_file_paths(backend, remote_dir)
    if not file_paths:
        raise ValueError(f"沙盒路径 {remote_dir} 中未发现可下载文件")

    responses = backend.download_files(file_paths)
    if len(responses) != len(file_paths):
        raise ValueError("沙盒文件下载结果数量不匹配")

    for expected_path, response in zip(file_paths, responses):
        error = getattr(response, "error", None)
        content = getattr(response, "content", None)
        if error or content is None:
            raise ValueError(f"下载沙盒文件失败: {expected_path} ({error or 'empty_content'})")

        pure_path = PurePosixPath(expected_path)
        try:
            relative_path = pure_path.relative_to(remote_root)
        except ValueError:
            relative_path = PurePosixPath(pure_path.name)

        target_path = local_dir / Path(relative_path.as_posix())
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)


def _prepare_skill_from_sandbox(sandbox_path: str, thread_id: str, uid: str, staging_root: Path) -> tuple[Path, str]:
    """从 Sandbox 路径准备 skill 目录。返回 (本地目录, 原始 skill name)。"""
    from yuxi.agents.backends.sandbox import ProvisionerSandboxBackend, resolve_virtual_path
    from yuxi.agents.skills.service import (
        _parse_skill_markdown,
        is_valid_skill_slug,
    )

    slug = PurePosixPath(sandbox_path.rstrip("/")).name
    if not is_valid_skill_slug(slug):
        raise ValueError(f"slug '{slug}' 不合法（仅允许小写字母、数字和连字符）")

    if not sandbox_path.startswith("/home/gem/user-data/"):
        raise ValueError(f"不支持的沙盒路径: {sandbox_path}。{SANDBOX_PATH_HINT}")

    staging = staging_root / slug

    # 优先尝试共享卷路径（性能更好，无需走沙盒 API）。
    try:
        local_path = resolve_virtual_path(thread_id, sandbox_path, uid=uid)
        if (local_path / "SKILL.md").exists():
            shutil.copytree(local_path, staging)
        else:
            raise FileNotFoundError(f"{local_path} 中未找到 SKILL.md")
    except FileNotFoundError:
        staging.mkdir(parents=True, exist_ok=True)
        backend = ProvisionerSandboxBackend(thread_id=thread_id, uid=uid)
        _download_skill_dir(backend, sandbox_path, staging)
        if not (staging / "SKILL.md").exists():
            raise ValueError(f"沙盒路径 {sandbox_path} 中未找到 SKILL.md")

    content = (staging / "SKILL.md").read_text(encoding="utf-8")
    parsed_name, _, _ = _parse_skill_markdown(content)
    return staging, parsed_name


async def _enable_skills_in_current_config(db, thread_id: str, uid: str, skill_slugs: list[str]) -> bool:
    """在当前会话绑定且当前用户拥有的 Agent 配置中启用新安装的 skill。"""
    conv_repo = ConversationRepository(db)
    conv = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conv or str(conv.uid) != str(uid):
        return False

    agent_repo = AgentRepository(db)
    agent = await agent_repo.get_by_slug(conv.agent_id)
    if not agent or agent.created_by != str(uid):
        return False

    config_json = dict(agent.config_json or {})
    context = dict(config_json.get("context") or {})
    skills = [item for item in context.get("skills") or [] if isinstance(item, str) and item.strip()]
    seen = set(skills)
    for slug in skill_slugs:
        if slug not in seen:
            skills.append(slug)
            seen.add(slug)
    context["skills"] = skills
    config_json["context"] = context
    await agent_repo.update(agent, config_json=config_json, updated_by=str(uid))
    return True


async def _run_install_task(
    source: str,
    runtime: ToolRuntime,
    tool_call_id: str,
    skill_names: list[str] | None = None,
) -> Command:
    """执行异步安装任务的核心逻辑。"""
    runtime_context = getattr(runtime, "context", None)
    if getattr(runtime_context, "is_subagent_runtime", False):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="错误：install_skill 只能在主智能体中使用，子智能体无法安装 Skill",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    source = str(source or "").strip()
    uid = getattr(runtime_context, "uid", None)
    thread_id = getattr(runtime_context, "thread_id", None)

    logger.info(f"install_skill called with uid={uid}, thread_id={thread_id}, source={source}")

    if not uid or not thread_id:
        return Command(
            update={"messages": [ToolMessage(content="错误：无法获取当前会话信息", tool_call_id=tool_call_id)]}
        )
    if not source:
        return Command(
            update={"messages": [ToolMessage(content="错误：Skill 来源不能为空", tool_call_id=tool_call_id)]}
        )

    personal_share_config = _personal_skill_share_config(uid)

    try:
        from yuxi.agents.skills.service import (
            import_skill_dir,
            normalize_string_list,
            sync_thread_readable_skills,
        )

        installed_slugs: list[str] = []
        failed_items: list[dict] = []
        slug_warnings: list[str] = []
        config_success = True

        if source.startswith("/"):
            with tempfile.TemporaryDirectory(prefix=".skill-install-") as tmp:
                source_dir, parsed_name = _prepare_skill_from_sandbox(source, thread_id, uid, Path(tmp))
                async with pg_manager.get_async_session_context() as db:
                    item = await import_skill_dir(
                        db,
                        source_dir=source_dir,
                        created_by=uid,
                        share_config=personal_share_config,
                    )
                    installed_slugs = [item.slug]
                    if item.slug != parsed_name:
                        slug_warnings.append(f"⚠️ 技能 slug '{item.slug}' 已存在，已自动重命名安装")
                    config_success = await _enable_skills_in_current_config(db, thread_id, uid, installed_slugs)
        else:
            _skill_names = skill_names or []
            if not _skill_names:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content="❌ 错误: 从 Git 安装时必须通过 skill_names 指定技能名称",
                                tool_call_id=tool_call_id,
                            )
                        ]
                    }
                )

            from yuxi.agents.skills.remote_install import prepare_remote_skills_batch

            preparation = await prepare_remote_skills_batch(source=source, skills=_skill_names)
            try:
                async with pg_manager.get_async_session_context() as db:
                    for result in preparation.results:
                        if not result.get("success"):
                            failed_items.append(result)
                            continue

                        try:
                            item = await import_skill_dir(
                                db,
                                source_dir=result["source_dir"],
                                created_by=uid,
                                share_config=personal_share_config,
                            )
                            installed_slugs.append(item.slug)
                        except Exception as e:
                            await db.rollback()
                            failed_items.append({"slug": result["slug"], "success": False, "error": str(e)})

                    config_success = True
                    if installed_slugs:
                        config_success = await _enable_skills_in_current_config(db, thread_id, uid, installed_slugs)
            finally:
                preparation.cleanup()

        current_skills = normalize_string_list(getattr(runtime_context, "skills", None))
        sync_thread_readable_skills(thread_id, normalize_string_list(current_skills + installed_slugs))

        lines = []
        if installed_slugs:
            lines.append(f"✅ 成功安装并激活技能: {', '.join(installed_slugs)}")
        for warning in slug_warnings:
            lines.append(warning)
        if failed_items:
            for item in failed_items:
                lines.append(f"❌ 安装失败 ({item['slug']}): {item.get('error', '未知错误')}")
        if not config_success:
            lines.append("⚠️ 已添加这个 Skill 拓展（仅当前会话生效，未写入当前 Agent 配置）")
        if not installed_slugs and not failed_items:
            lines.append("ℹ️ 未发现需要安装的技能")

        return Command(
            update={
                "activated_skills": installed_slugs,
                "messages": [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
            }
        )

    except Exception as e:
        logger.exception("install_skill 异常")
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"❌ 安装异常: {str(e)}",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )


@tool(
    category="buildin",
    tags=["skill", "安装"],
    display_name="安装技能",
    args_schema=InstallSkillInput,
)
async def install_skill(
    source: str,
    skill_names: list[str] | None = None,
    runtime: ToolRuntime = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """安装新的 Skill 到当前用户的私有空间，并在当前主智能体会话中激活。"""
    return await _run_install_task(source, runtime, tool_call_id, skill_names)
