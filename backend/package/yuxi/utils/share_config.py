from __future__ import annotations

from collections.abc import Collection

SHARE_ACCESS_LEVELS = {"global", "department", "user"}
EMPTY_SHARE_CONFIG = {"access_level": "global", "department_ids": [], "user_uids": []}


def _normalize_department_ids(department_ids: list | None) -> list[int]:
    return [int(department_id) for department_id in department_ids or []]


def _normalize_user_uids(user_uids: list | None) -> list[str]:
    return [uid for uid in (str(uid).strip() for uid in user_uids or []) if uid]


def normalize_share_config(
    share_config: dict | None,
    *,
    default_config: dict | None,
    default_access_level: str,
    invalid_access_level_message: str,
    user_uid: str | None = None,
    department_id: int | str | None = None,
    allowed_access_levels: Collection[str] | None = None,
    unauthorized_access_level_message: str | None = None,
) -> dict:
    config = share_config or default_config or {}
    access_level = config.get("access_level") or default_access_level
    if access_level not in SHARE_ACCESS_LEVELS:
        raise ValueError(invalid_access_level_message)
    if allowed_access_levels is not None and access_level not in allowed_access_levels:
        raise ValueError(unauthorized_access_level_message or invalid_access_level_message)

    if access_level == "global":
        return EMPTY_SHARE_CONFIG.copy()

    if access_level == "department":
        department_ids = _normalize_department_ids(config.get("department_ids"))
        if department_id is not None:
            department_ids.append(int(department_id))
        department_ids = sorted(set(department_ids))
        if not department_ids:
            raise ValueError("部门共享至少需要选择一个部门")
        return {"access_level": "department", "department_ids": department_ids, "user_uids": []}

    user_uids = _normalize_user_uids(config.get("user_uids"))
    if user_uid:
        user_uids.append(str(user_uid))
    user_uids = sorted(set(user_uids))
    if not user_uids:
        raise ValueError("指定人至少需要选择一个用户")
    return {"access_level": "user", "department_ids": [], "user_uids": user_uids}
