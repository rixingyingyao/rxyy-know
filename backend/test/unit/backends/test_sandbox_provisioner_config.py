from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_NAME = "sandbox_provisioner_app_for_test"


def _find_module_path() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "docker" / "sandbox_provisioner" / "app.py"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("docker/sandbox_provisioner/app.py not found from test path")


MODULE_PATH = _find_module_path()


def _load_module():
    existing = sys.modules.get(MODULE_NAME)
    if existing is not None:
        return existing

    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def test_canonical_backend_name(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    assert module.canonical_backend_name("docker") == "docker"
    assert module.canonical_backend_name("kubernetes") == "kubernetes"


def test_merged_sandbox_env_user_values_override_global(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    assert module.merged_sandbox_env(
        {"SHARED": "global", "GLOBAL_ONLY": "value"},
        {"SHARED": "user", "USER_ONLY": "value"},
    ) == {
        "SHARED": "user",
        "GLOBAL_ONLY": "value",
        "USER_ONLY": "value",
    }


def test_normalize_env_converts_values_to_strings(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    assert module.normalize_env({"A": 1, "B": None, "": "ignored"}) == {"A": "1", "B": ""}


def test_local_container_identity_validation_rejects_unsafe_path_segments(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend_cls = module.LocalContainerProvisionerBackend

    assert backend_cls._validate_thread_id("thread-1_2") == "thread-1_2"
    assert backend_cls._validate_uid("user-1_2") == "user-1_2"

    for value in ["../escape", "thread/name", "thread name", "thread;rm", "thread.name"]:
        with pytest.raises(ValueError):
            backend_cls._validate_thread_id(value)

    for value in ["../user", "user/name", "user name", "user;rm", "user.name"]:
        with pytest.raises(ValueError):
            backend_cls._validate_uid(value)


def test_memory_backend_accepts_split_thread_ids(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend = module.MemoryProvisionerBackend()

    record = backend.create(
        "sandbox-1",
        "child-thread",
        "user-1",
        file_thread_id="parent-thread",
        skills_thread_id="child-skills-thread",
    )

    assert record.sandbox_id == "sandbox-1"
    assert backend.discover("sandbox-1") is record


def test_docker_mount_checks_use_file_and_skills_thread_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._threads_host_path = str(tmp_path)

    workspace = tmp_path / "shared" / "user-1" / "workspace"
    uploads = tmp_path / "parent-thread" / "user-data" / "uploads"
    outputs = tmp_path / "parent-thread" / "user-data" / "outputs"
    skills = tmp_path / "child-skills-thread" / "skills"
    container = SimpleNamespace(
        attrs={
            "Mounts": [
                {"Destination": "/home/gem/user-data/workspace", "Source": str(workspace)},
                {"Destination": "/home/gem/user-data/uploads", "Source": str(uploads)},
                {"Destination": "/home/gem/user-data/outputs", "Source": str(outputs)},
                {"Destination": "/home/gem/skills", "Source": str(skills)},
            ]
        }
    )

    assert backend._has_expected_user_data_mounts(container, "parent-thread", "user-1") is True
    assert backend._is_expected_skills_mount(container, "child-skills-thread") is True
    assert backend._has_expected_user_data_mounts(container, "child-thread", "user-1") is False
    assert backend._is_expected_skills_mount(container, "parent-thread") is False


def test_kubernetes_mount_check_uses_file_and_skills_thread_ids(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    pod = SimpleNamespace(
        spec=SimpleNamespace(
            containers=[
                SimpleNamespace(
                    name="sandbox",
                    volume_mounts=[
                        SimpleNamespace(
                            mount_path="/home/gem/user-data/workspace",
                            sub_path="threads/shared/user-1/workspace",
                        ),
                        SimpleNamespace(
                            mount_path="/home/gem/user-data/uploads",
                            sub_path="threads/parent-thread/user-data/uploads",
                        ),
                        SimpleNamespace(
                            mount_path="/home/gem/user-data/outputs",
                            sub_path="threads/parent-thread/user-data/outputs",
                        ),
                        SimpleNamespace(mount_path="/home/gem/skills", sub_path="threads/child-skills-thread/skills"),
                    ],
                )
            ]
        )
    )

    assert module.KubernetesProvisionerBackend._pod_has_expected_mounts(
        pod,
        file_thread_id="parent-thread",
        skills_thread_id="child-skills-thread",
        uid="user-1",
    )
    assert not module.KubernetesProvisionerBackend._pod_has_expected_mounts(
        pod,
        file_thread_id="child-thread",
        skills_thread_id="child-skills-thread",
        uid="user-1",
    )
