from __future__ import annotations

import pytest

from yuxi.utils.share_config import normalize_share_config


def test_normalize_share_config_defaults_to_global() -> None:
    result = normalize_share_config(
        None,
        default_config={"access_level": "global", "department_ids": [], "user_uids": []},
        default_access_level="global",
        invalid_access_level_message="无效的权限等级",
    )

    assert result == {"access_level": "global", "department_ids": [], "user_uids": []}


def test_normalize_share_config_department_adds_actor_department_and_deduplicates() -> None:
    result = normalize_share_config(
        {"access_level": "department", "department_ids": ["2", 1], "user_uids": ["ignored"]},
        default_config={"access_level": "global", "department_ids": [], "user_uids": []},
        default_access_level="global",
        invalid_access_level_message="无效的权限等级",
        department_id="2",
    )

    assert result == {"access_level": "department", "department_ids": [1, 2], "user_uids": []}


def test_normalize_share_config_user_adds_actor_and_deduplicates() -> None:
    result = normalize_share_config(
        {"access_level": "user", "department_ids": [1], "user_uids": [" other ", "owner", ""]},
        default_config={"access_level": "user", "department_ids": [], "user_uids": []},
        default_access_level="user",
        invalid_access_level_message="无效的权限等级",
        user_uid="owner",
    )

    assert result == {"access_level": "user", "department_ids": [], "user_uids": ["other", "owner"]}


def test_normalize_share_config_rejects_disallowed_access_level() -> None:
    with pytest.raises(ValueError, match="无权使用该共享范围"):
        normalize_share_config(
            {"access_level": "global", "department_ids": [], "user_uids": []},
            default_config={"access_level": "user", "department_ids": [], "user_uids": []},
            default_access_level="user",
            invalid_access_level_message="无效的权限等级",
            allowed_access_levels={"user"},
            unauthorized_access_level_message="无权使用该共享范围",
        )
