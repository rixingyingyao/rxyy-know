"""
标签体系 CRUD 管理

操作 src/services/tagging/taxonomy_data.json，
支持添加/修改/删除(归档)/移动/同义词/搜索/导入/导出。
变更日志记录到 saves/config/taxonomy_changelog.json。
"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from yuxi.utils import logger

_TAXONOMY_FILE = Path(__file__).parent / "taxonomy_data.json"
_CHANGELOG_DIR = Path(os.getenv("SAVE_DIR", "saves")) / "config"
_CHANGELOG_FILE = _CHANGELOG_DIR / "taxonomy_changelog.json"
_lock = asyncio.Lock()


def _load_taxonomy() -> dict:
    with open(_TAXONOMY_FILE, encoding="utf-8") as f:
        return json.load(f)


async def _save_taxonomy(data: dict) -> None:
    """原子写入标签体系"""
    async with _lock:
        tmp = _TAXONOMY_FILE.with_suffix(".tmp")
        content = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(_TAXONOMY_FILE)


async def _log_change(action: str, node_id: str, detail: str) -> None:
    """记录变更日志"""
    _CHANGELOG_DIR.mkdir(parents=True, exist_ok=True)
    changelog = []
    if _CHANGELOG_FILE.exists():
        try:
            with open(_CHANGELOG_FILE, encoding="utf-8") as f:
                changelog = json.load(f)
        except Exception:
            pass

    changelog.append({
        "action": action,
        "node_id": node_id,
        "detail": detail,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })

    # 只保留最近 500 条
    changelog = changelog[-500:]
    tmp = _CHANGELOG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(changelog, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_CHANGELOG_FILE)


def get_tree() -> list[dict]:
    """获取完整标签树"""
    data = _load_taxonomy()
    return data.get("nodes", [])


def get_node(node_id: str) -> dict | None:
    """获取单个节点"""
    data = _load_taxonomy()
    for n in data.get("nodes", []):
        if n["id"] == node_id:
            return n
    return None


def search_nodes(query: str, include_archived: bool = False) -> list[dict]:
    """搜索标签节点"""
    data = _load_taxonomy()
    results = []
    q = query.lower()
    for n in data.get("nodes", []):
        if not include_archived and n.get("archived", False):
            continue
        name_zh = n.get("name_zh", "").lower()
        name_en = n.get("name_en", "").lower()
        synonyms = [s.lower() for s in n.get("synonyms", [])]
        if q in name_zh or q in name_en or q in n.get("id", "").lower() or any(q in s for s in synonyms):
            results.append(n)
    return results


async def add_node(
    name_zh: str,
    parent_id: str | None = None,
    name_en: str = "",
    source: str = "custom",
    dimension: str = "",
) -> dict:
    """添加标签节点"""
    data = _load_taxonomy()
    nodes = data.get("nodes", [])

    # 确定层级和路径
    level = 1
    path = name_zh
    if parent_id:
        parent = next((n for n in nodes if n["id"] == parent_id), None)
        if not parent:
            raise ValueError(f"Parent node {parent_id} not found")
        level = parent["level"] + 1
        path = f"{parent['path']} > {name_zh}"
        # 继承父节点维度
        if not dimension:
            dimension = parent.get("dimension", "topic")

    # 生成 ID
    node_id = f"CU{str(uuid.uuid4())[:6].upper()}"

    now = time.strftime("%Y-%m-%d")
    node = {
        "id": node_id,
        "name_zh": name_zh,
        "name_en": name_en,
        "level": level,
        "parent_id": parent_id,
        "source": source,
        "path": path,
        "dimension": dimension or "topic",
        "archived": False,
        "synonyms": [],
        "usage_count": 0,
        "created_at": now,
        "updated_at": now,
    }

    nodes.append(node)
    data["nodes"] = nodes
    data["total_nodes"] = len([n for n in nodes if not n.get("archived", False)])
    await _save_taxonomy(data)
    await _log_change("add", node_id, f"添加节点: {name_zh}")
    return node


async def update_node(node_id: str, updates: dict) -> dict | None:
    """更新节点（支持 name_zh, name_en）"""
    data = _load_taxonomy()
    nodes = data.get("nodes", [])

    target = None
    for n in nodes:
        if n["id"] == node_id:
            target = n
            break

    if not target:
        return None

    allowed = {"name_zh", "name_en"}
    for k, v in updates.items():
        if k in allowed:
            target[k] = v

    # 更新路径
    if "name_zh" in updates:
        target["path"] = _rebuild_path(target, nodes)
        # 更新子节点路径
        _update_children_paths(node_id, nodes)

    target["updated_at"] = time.strftime("%Y-%m-%d")
    data["nodes"] = nodes
    await _save_taxonomy(data)
    await _log_change("update", node_id, f"更新节点: {json.dumps(updates, ensure_ascii=False)}")
    return target


async def archive_node(node_id: str) -> dict | None:
    """彻底删除节点及其所有子节点"""
    data = _load_taxonomy()
    nodes = data.get("nodes", [])

    target = next((n for n in nodes if n["id"] == node_id), None)
    if not target:
        return None

    # 收集该节点及所有子孙节点的 ID
    ids_to_delete = set()
    queue = [node_id]
    while queue:
        nid = queue.pop(0)
        ids_to_delete.add(nid)
        for n in nodes:
            if n.get("parent_id") == nid and n["id"] not in ids_to_delete:
                queue.append(n["id"])

    deleted_names = [n["name_zh"] for n in nodes if n["id"] in ids_to_delete]
    remaining = [n for n in nodes if n["id"] not in ids_to_delete]

    data["nodes"] = remaining
    data["total_nodes"] = len(remaining)
    data["l1_count"] = sum(1 for n in remaining if n.get("level") == 1)
    data["l2_count"] = sum(1 for n in remaining if n.get("level") == 2)
    await _save_taxonomy(data)
    await _log_change("delete", node_id, f"删除节点: {', '.join(deleted_names)}")
    return {"deleted": len(ids_to_delete), "names": deleted_names}


async def move_node(node_id: str, new_parent_id: str | None) -> dict | None:
    """移动节点到新的父级"""
    data = _load_taxonomy()
    nodes = data.get("nodes", [])

    target = next((n for n in nodes if n["id"] == node_id), None)
    if not target:
        return None

    if new_parent_id:
        new_parent = next((n for n in nodes if n["id"] == new_parent_id), None)
        if not new_parent:
            raise ValueError(f"New parent {new_parent_id} not found")
        target["parent_id"] = new_parent_id
        target["level"] = new_parent["level"] + 1
    else:
        target["parent_id"] = None
        target["level"] = 1

    target["path"] = _rebuild_path(target, nodes)
    target["updated_at"] = time.strftime("%Y-%m-%d")
    _update_children_paths(node_id, nodes)

    data["nodes"] = nodes
    await _save_taxonomy(data)
    await _log_change("move", node_id, f"移动节点 {target['name_zh']} → parent={new_parent_id}")
    return target


async def update_synonyms(node_id: str, synonyms: list[str]) -> dict | None:
    """更新同义词"""
    data = _load_taxonomy()
    for n in data.get("nodes", []):
        if n["id"] == node_id:
            n["synonyms"] = synonyms
            n["updated_at"] = time.strftime("%Y-%m-%d")
            await _save_taxonomy(data)
            await _log_change("synonyms", node_id, f"同义词: {synonyms}")
            return n
    return None


def export_taxonomy() -> dict:
    """导出完整标签体系"""
    return _load_taxonomy()


async def import_taxonomy(imported: dict, merge: bool = True) -> dict:
    """导入标签体系（merge=True 时合并，False 时替换）"""
    if not merge:
        await _save_taxonomy(imported)
        await _log_change("import", "*", "完整替换导入")
        return {"imported": len(imported.get("nodes", []))}

    data = _load_taxonomy()
    existing_ids = {n["id"] for n in data.get("nodes", [])}
    new_count = 0
    for node in imported.get("nodes", []):
        if node["id"] not in existing_ids:
            data["nodes"].append(node)
            new_count += 1

    data["total_nodes"] = len([n for n in data["nodes"] if not n.get("archived", False)])
    await _save_taxonomy(data)
    await _log_change("import", "*", f"合并导入 {new_count} 个节点")
    return {"imported": new_count}


def _rebuild_path(node: dict, nodes: list[dict]) -> str:
    """重建节点路径"""
    parts = [node["name_zh"]]
    current = node
    while current.get("parent_id"):
        parent = next((n for n in nodes if n["id"] == current["parent_id"]), None)
        if not parent:
            break
        parts.insert(0, parent["name_zh"])
        current = parent
    return " > ".join(parts)


def _update_children_paths(parent_id: str, nodes: list[dict]) -> None:
    """递归更新子节点路径"""
    for n in nodes:
        if n.get("parent_id") == parent_id:
            n["path"] = _rebuild_path(n, nodes)
            _update_children_paths(n["id"], nodes)
