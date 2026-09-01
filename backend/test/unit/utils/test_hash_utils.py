from __future__ import annotations

import hashlib

from yuxi.utils.hash_utils import hash_id, hashstr, subagent_child_thread_id


def test_hashstr_matches_sha256_hex() -> None:
    assert hashstr("abc") == hashlib.sha256(b"abc").hexdigest()


def test_hashstr_supports_length_and_salt() -> None:
    expected = hashlib.sha256(b"abc-fixed").hexdigest()[:12]

    assert hashstr("abc", length=12, with_salt=True, salt="-fixed") == expected


def test_hash_id_generates_stable_prefixed_id() -> None:
    hashed = hash_id("subagent:", "parent-run:child-thread:tool-1:worker")

    assert hashed.startswith("subagent:")
    assert len(hashed) == 48
    assert hashed == hash_id("subagent:", "parent-run:child-thread:tool-1:worker")


def test_hash_id_length_includes_prefix() -> None:
    hashed = hash_id("subagent_", "parent-thread:worker:tool-1", length=64)

    assert hashed.startswith("subagent_")
    assert len(hashed) == 64


def test_subagent_child_thread_id_matches_inline_formula() -> None:
    expected = hash_id("subagent_", "parent-thread:worker:tool-1", length=64)

    assert subagent_child_thread_id("parent-thread", "worker", "tool-1") == expected
    assert len(subagent_child_thread_id("parent-thread", "worker", "tool-1")) == 64
