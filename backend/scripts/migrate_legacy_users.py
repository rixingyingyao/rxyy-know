# -*- coding: utf-8 -*-
"""迁移脚本：旧版本 SQLite 用户/部门 → 当前版本 PostgreSQL

用法（在 api 容器内执行）：
    docker compose exec api uv run --no-sync python scripts/migrate_legacy_users.py /app/saves/legacy_server.db --dry-run
    docker compose exec api uv run --no-sync python scripts/migrate_legacy_users.py /app/saves/legacy_server.db

前置：把旧环境 saves/database/server.db 拷贝到新环境挂载目录（如 docker/volumes/yuxi/legacy_server.db）。

行为：
- 部门按 name 幂等 upsert；用户按 uid 幂等（已存在跳过）
- 旧表字段 user_id/username/password_hash/role/department_id 映射到新 uid 语义
- 密码 hash 原样搬迁（两代都是 bcrypt/pbkdf2 由 AuthUtils 校验，不重置）
- 不迁对话历史（业务决策：旧对话留旧环境只读）
"""

import argparse
import asyncio
import sqlite3
import sys


def read_legacy(db_path: str) -> tuple[list[dict], list[dict]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    departments = [dict(r) for r in cur.execute("SELECT * FROM departments").fetchall()]
    users = [dict(r) for r in cur.execute("SELECT * FROM users WHERE is_deleted IS NOT 1").fetchall()]
    con.close()
    return departments, users


async def migrate(db_path: str, dry_run: bool) -> None:
    from yuxi.repositories.department_repository import DepartmentRepository
    from yuxi.repositories.user_repository import UserRepository
    from yuxi.utils import logger

    departments, users = read_legacy(db_path)
    logger.info(f"legacy: {len(departments)} departments, {len(users)} users")

    dept_repo = DepartmentRepository()
    user_repo = UserRepository()

    # 部门映射：旧 id -> 新 id
    dept_id_map: dict[int, int] = {}
    existing_departments = {d.name: d for d in await dept_repo.list_departments()}
    for dept in departments:
        name = dept.get("name") or "默认部门"
        if name in existing_departments:
            dept_id_map[dept["id"]] = existing_departments[name].id
            logger.info(f"dept exists: {name} -> {existing_departments[name].id}")
            continue
        if dry_run:
            logger.info(f"[dry-run] would create dept: {name}")
            continue
        created = await dept_repo.create({"name": name, "description": dept.get("description") or ""})
        dept_id_map[dept["id"]] = created.id
        logger.info(f"dept created: {name} -> {created.id}")

    default_dept = next(iter(dept_id_map.values()), None)
    if default_dept is None:
        existing = await dept_repo.list_departments()
        default_dept = existing[0].id if existing else None

    migrated = skipped = 0
    for u in users:
        # 旧代 user_id / 新代 uid 兼容
        uid = str(u.get("user_id") or u.get("uid") or u.get("username") or "").strip()
        if not uid:
            logger.warning(f"skip user without uid: {u.get('id')}")
            continue
        exists = await user_repo.get_by_uid(uid)
        if exists:
            skipped += 1
            continue
        if dry_run:
            logger.info(f"[dry-run] would create user: {uid} role={u.get('role')}")
            migrated += 1
            continue
        await user_repo.create(
            {
                "uid": uid,
                "username": u.get("username") or uid,
                "phone_number": u.get("phone_number"),
                "avatar": None,
                "password_hash": u.get("password_hash"),
                "role": u.get("role") or "user",
                "department_id": dept_id_map.get(u.get("department_id"), default_dept),
            }
        )
        migrated += 1
        logger.info(f"user migrated: {uid} ({u.get('role')})")

    logger.info(f"DONE migrated={migrated} skipped(existing)={skipped}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path", help="旧 SQLite server.db 路径")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(migrate(args.db_path, args.dry_run)))
