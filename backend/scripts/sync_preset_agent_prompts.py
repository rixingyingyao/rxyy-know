# -*- coding: utf-8 -*-
"""运维脚本：把 preset_prompts.py 最新提示词同步到已落库的预置智能体

`ensure_preset_agents` 为避免覆盖管理员修改，对已存在记录不更新；
当 preset_prompts.py 迭代后需手动执行本脚本刷新 DB 中的 system_prompt。

用法（在 api 容器内执行）：
    docker compose exec api uv run --no-sync python scripts/sync_preset_agent_prompts.py
"""

import asyncio


async def main() -> None:
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from yuxi.agents.preset_prompts import (
        MEDIA_AGENT_SLUG,
        MEDIA_AGENT_SYSTEM_PROMPT,
        SYSTEM_PROMPT as TOPIC_GRAPH_SYSTEM_PROMPT,
        TOPIC_GRAPH_AGENT_SLUG,
    )
    from yuxi.repositories.agent_repository import (
        DEEP_RESEARCH_AGENT_SLUG,
        DEEP_RESEARCH_SYSTEM_PROMPT,
    )
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import Agent
    from yuxi.utils import logger

    targets = {
        TOPIC_GRAPH_AGENT_SLUG: TOPIC_GRAPH_SYSTEM_PROMPT,
        MEDIA_AGENT_SLUG: MEDIA_AGENT_SYSTEM_PROMPT,
        DEEP_RESEARCH_AGENT_SLUG: DEEP_RESEARCH_SYSTEM_PROMPT,
    }

    async with pg_manager.get_async_session_context() as session:
        for slug, prompt in targets.items():
            agent = (await session.execute(select(Agent).where(Agent.slug == slug))).scalar_one_or_none()
            if agent is None:
                logger.warning(f"agent {slug} 不存在，跳过（先启动服务触发 ensure_preset_agents）")
                continue
            config = dict(agent.config_json or {})
            context = dict(config.get("context") or {})
            old = context.get("system_prompt") or ""
            if old == prompt:
                logger.info(f"agent {slug}: prompt 已是最新（{len(prompt)} chars）")
                continue
            context["system_prompt"] = prompt
            config["context"] = context
            agent.config_json = config
            flag_modified(agent, "config_json")
            session.add(agent)
            logger.info(f"agent {slug}: prompt 已更新 {len(old)} -> {len(prompt)} chars")


if __name__ == "__main__":
    asyncio.run(main())
