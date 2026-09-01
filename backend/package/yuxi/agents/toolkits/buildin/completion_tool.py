"""Knowledge base completion tool · agent 调用的数据补齐工具

设计：
1. agent 在 prompt 中检测到数据不足时输出 needs_completion 提示
2. 用户授权后，agent 调用本工具
3. 工具：抓取公开网页（仅深圳新闻网）→ 转 markdown → 通过本机 HTTP API 上传到知识库 → 触发入库任务
4. 同步轮询入库任务 task_id 直到完成，最多等 `wait_seconds`，再返回真实结果

依赖：
- 环境变量 YUXI_SUPER_ADMIN_NAME / YUXI_SUPER_ADMIN_PASSWORD（已配在 .env）
- 本机 API http://localhost:5050
- 任务状态接口 GET /api/tasks/{task_id} 返回 status: pending/running/success/failed/cancelled
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urljoin, urlparse

import httpx
import requests
from bs4 import BeautifulSoup

from yuxi.agents.toolkits.registry import tool
from yuxi.utils import logger

API_BASE = os.getenv("YUXI_API_BASE", "http://localhost:5050/api")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
}
TIMEOUT = 20

_TOKEN_CACHE = {"token": None, "expires_at": 0}


def _get_admin_token() -> str | None:
    """获取 super admin token，缓存 50 分钟"""
    now = time.time()
    if _TOKEN_CACHE["token"] and _TOKEN_CACHE["expires_at"] > now + 60:
        return _TOKEN_CACHE["token"]

    username = os.getenv("YUXI_SUPER_ADMIN_NAME")
    password = os.getenv("YUXI_SUPER_ADMIN_PASSWORD")
    if not username or not password:
        logger.warning("YUXI_SUPER_ADMIN_NAME / PASSWORD 未配置，补齐工具不可用")
        return None

    try:
        r = httpx.post(
            f"{API_BASE}/auth/token",
            data={"username": username, "password": password},
            timeout=15,
        )
        if r.status_code == 200:
            tok = r.json().get("access_token")
            _TOKEN_CACHE["token"] = tok
            _TOKEN_CACHE["expires_at"] = now + 50 * 60
            return tok
        logger.error(f"admin token 获取失败: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.error(f"admin token 请求异常: {e}")
    return None


SZNEWS_THEME_PAGES = {
    "前海": ["https://iqianhai.sznews.com/", "https://www.sznews.com/"],
    "光明": ["https://iguangming.sznews.com/", "https://www.sznews.com/"],
    "光明科学城": ["https://iguangming.sznews.com/", "https://www.sznews.com/"],
    "福田": ["https://ifutian.sznews.com/", "https://www.sznews.com/"],
    "南山": ["https://inanshan.sznews.com/", "https://www.sznews.com/"],
    "宝安": ["https://ibaoan.sznews.com/", "https://www.sznews.com/"],
    "龙岗": ["https://ilonggang.sznews.com/", "https://www.sznews.com/"],
    "龙华": ["https://ilonghua.sznews.com/", "https://www.sznews.com/"],
    "坪山": ["https://ipingshan.sznews.com/", "https://www.sznews.com/"],
    "盐田": ["https://iyantian.sznews.com/", "https://www.sznews.com/"],
    "大鹏": ["https://idapeng.sznews.com/", "https://www.sznews.com/"],
}

ARTICLE_URL_RE = re.compile(r"/content/\d{4}-\d{2}/\d{2}/content_\d+\.htm")


def _http_get(url: str) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.encoding = r.apparent_encoding or "utf-8"
        if r.status_code == 200 and len(r.text) > 500:
            return r.text
    except Exception as e:
        logger.debug(f"fetch fail {url}: {e}")
    return None


def _collect_article_urls(entity: str, max_pages: int = 5) -> list[str]:
    """从相关专题页 + 全站首页收集含 entity 的 article URL"""
    list_pages = []
    for keyword, pages in SZNEWS_THEME_PAGES.items():
        if keyword in entity:
            list_pages.extend(pages)
            break
    if not list_pages:
        list_pages = ["https://www.sznews.com/"]

    urls: list[str] = []
    seen: set[str] = set()
    for lst in list_pages[:max_pages]:
        html = _http_get(lst)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if not href:
                continue
            href = urljoin(lst, href)
            p = urlparse(href)
            if not p.netloc.endswith("sznews.com"):
                continue
            if not ARTICLE_URL_RE.search(p.path):
                continue
            if href in seen:
                continue
            seen.add(href)
            link_text = a.get_text(" ", strip=True)
            if entity in link_text or "标题" in link_text:
                urls.append(href)
        time.sleep(0.3)
    return urls


def _extract_article(html: str, url: str = "") -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title and soup.title:
        title = soup.title.get_text(strip=True).split("_")[0].strip()
    if not title:
        return None

    pub_time = ""
    for sel in [".pubTime", ".time", "#pubtime_baidu", ".article-time", ".info"]:
        e = soup.select_one(sel)
        if e:
            txt = e.get_text(" ", strip=True)
            m = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", txt)
            if m:
                pub_time = m.group(0).replace("/", "-")
                break
    if not pub_time and url:
        m = re.search(r"/content/(\d{4})-(\d{2})/(\d{2})/", url)
        if m:
            pub_time = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    body_candidates = [
        "#article-content", ".article-content", "#content", ".content", "#zoom",
        ".articleCon", ".text", "article",
        ".zhengwen", ".gm_article", ".pad260", ".index-content",
    ]
    body = None
    for sel in body_candidates:
        b = soup.select_one(sel)
        if b and len(b.get_text(strip=True)) > 150:
            body = b
            break
    if not body:
        body = soup.body
    if not body:
        return None

    for tag in body.find_all(["script", "style", "iframe", "noscript", "ins"]):
        tag.decompose()
    for tag in body.find_all(class_=re.compile(r"(share|comment|footer|nav|ad|related|recommend)", re.I)):
        tag.decompose()

    paragraphs: list[str] = []
    for p in body.find_all(["p", "h2", "h3"]):
        text = re.sub(r"\s+", " ", p.get_text(" ", strip=True))
        if len(text) >= 8:
            paragraphs.append(text)
    if not paragraphs:
        raw = body.get_text("\n", strip=True)
        for line in raw.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if len(line) >= 12 and not re.match(r"^[\d:\-\s]+$", line):
                paragraphs.append(line)
    if not paragraphs:
        return None

    return {
        "title": title.strip(),
        "pub_time": pub_time or "未知",
        "body": "\n\n".join(paragraphs),
    }


def _title_to_slug(title: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", title)[:50].strip("_")
    return s or "untitled"


def _save_article_to_kb(kb_id: str, url: str, article: dict, token: str) -> dict | None:
    """通过 HTTP API 上传 markdown 文件到知识库"""
    slug = _title_to_slug(article["title"])
    fname = f"{article['pub_time']}_{slug}.md"

    content = f"""---
title: {article['title']}
publish_time: {article['pub_time']}
source_url: {url}
source: 深圳新闻网（数据补齐）
crawled_at: {datetime.now().isoformat(timespec='seconds')}
---

# {article['title']}

**发布时间：** {article['pub_time']}
**来源：** [深圳新闻网]({url})

---

{article['body']}
"""

    try:
        r = httpx.post(
            f"{API_BASE}/knowledge/files/upload",
            params={"kb_id": kb_id},
            files={"file": (fname, content.encode("utf-8"), "text/markdown")},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()
        if r.status_code == 409:
            logger.info(f"[补齐] 文件已存在跳过 {fname}: {r.text[:200]}")
            return {"_skipped": True, "reason": "duplicate", "filename": fname}
        logger.warning(f"upload fail {fname}: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.warning(f"upload exception {fname}: {e}")
    return None


def _trigger_ingest(
    kb_id: str,
    file_paths: list[str],
    token: str,
    content_hashes: dict[str, str] | None = None,
) -> dict | None:
    """触发知识库入库（异步任务）。

    若 items 是 minio URL，后端 prepare_item_metadata 会强制要求 params.content_hashes 提供 {url: hash}
    映射，否则单条 item 会以"Missing content_hash"失败被静默吞掉、整体 task 仍 success 但 failed_count 涨。
    """
    params: dict[str, Any] = {"content_type": "file", "auto_index": True}
    if content_hashes:
        params["content_hashes"] = content_hashes
    try:
        r = httpx.post(
            f"{API_BASE}/knowledge/databases/{kb_id}/documents",
            json={"items": file_paths, "params": params},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()
        logger.warning(f"ingest fail: {r.status_code} {r.text[:300]}")
    except Exception as e:
        logger.warning(f"ingest exception: {e}")
    return None


TERMINAL_TASK_STATUSES = {"success", "failed", "cancelled"}


def _wait_task_done(task_id: str, token: str, wait_seconds: int = 600, poll_interval: int = 5) -> dict[str, Any]:
    """同步轮询 task 直到终态（success/failed/cancelled）或超时。

    返回 dict：
      - status: success / failed / cancelled / timeout / unknown
      - progress: 0-100
      - message: 任务最新进度消息
      - result: 任务成功时含 items 等详细数据（仅 success 状态有）
      - error: 失败原因
      - elapsed_seconds: 实际等待时长
    """
    started = time.time()
    deadline = started + wait_seconds
    last_status = "pending"
    last_progress = 0.0
    last_message = ""
    last_result: Any = None
    last_error: str | None = None

    while time.time() < deadline:
        try:
            r = httpx.get(
                f"{API_BASE}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if r.status_code == 200:
                task = r.json().get("task", {}) or {}
                last_status = task.get("status", "unknown")
                last_progress = task.get("progress", last_progress)
                last_message = task.get("message", last_message)
                last_result = task.get("result")
                last_error = task.get("error")
                if last_status in TERMINAL_TASK_STATUSES:
                    return {
                        "status": last_status,
                        "progress": last_progress,
                        "message": last_message,
                        "result": last_result,
                        "error": last_error,
                        "elapsed_seconds": round(time.time() - started, 1),
                    }
            elif r.status_code == 404:
                return {
                    "status": "unknown",
                    "progress": last_progress,
                    "message": "任务不存在或已被清理",
                    "elapsed_seconds": round(time.time() - started, 1),
                }
            else:
                logger.warning(f"poll task {task_id} fail: {r.status_code} {r.text[:200]}")
        except Exception as e:
            logger.warning(f"poll task {task_id} exception: {e}")
        time.sleep(poll_interval)

    return {
        "status": "timeout",
        "progress": last_progress,
        "message": last_message or f"入库任务执行中（已 {wait_seconds}s 未结束），可稍后到任务中心查看",
        "result": last_result,
        "error": last_error,
        "elapsed_seconds": round(time.time() - started, 1),
    }


@tool(
    category="buildin",
    tags=["knowledgebase", "web"],
    display_name="知识库数据补齐",
    config_guide="依赖环境变量 YUXI_SUPER_ADMIN_NAME / YUXI_SUPER_ADMIN_PASSWORD；数据源固定为深圳新闻网 sznews.com。",
    name_or_callable="complete_kb_from_web",
    description=(
        "从公开网页（仅深圳新闻网 sznews.com）抓取指定实体的相关稿件，自动上传到知识库并完整执行入库流程。"
        "用途：当知识库中某个实体（人物/平台/事件/主题）相关数据不足以画图时，用本工具补充数据。"
        "重要：仅在用户明确授权（同意补齐）后才能调用，不要默认调用。"
        "本工具会同步等待入库任务完成，通常 2-10 分钟。"
        "参数：entity=要补齐数据的实体名（如 '光明科学城'），kb_id=目标知识库 ID，count=希望抓取的篇数（5-20，默认 10），wait_seconds=最长等待入库完成的秒数（默认 600=10 分钟，超时会返回 timeout 状态但任务仍在后台跑）。"
        "返回结果含 status（completed/timeout/failed 等）、uploaded_count、task_id、sample_titles、ingest_status、indexed_count、failed_count、elapsed_seconds。"
    ),
)
def complete_kb_from_web(
    entity: Annotated[str, "要补齐数据的实体名（如 '前海合作区' / '光明科学城' / '改革开放'）"],
    kb_id: Annotated[str, "目标知识库 ID（如 kb_4e4123a7cfea68b54a9c9c24d16275c8）"],
    count: Annotated[int, "希望抓取的稿件数量 5-20"] = 10,
    wait_seconds: Annotated[int, "同步等待入库完成的最长秒数，超时仍会返回但任务继续后台执行"] = 600,
) -> dict[str, Any]:
    """从深圳新闻网抓取实体相关稿件 → 上传 → 触发入库 → 同步轮询入库任务直到完成或超时。"""
    token = _get_admin_token()
    if not token:
        return {
            "status": "failed",
            "error": "管理员凭证未配置（YUXI_SUPER_ADMIN_NAME/PASSWORD），补齐功能暂不可用",
        }

    count = max(3, min(count, 20))
    wait_seconds = max(60, min(wait_seconds, 1800))
    logger.info(f"[补齐] entity={entity} kb_id={kb_id} count={count} wait={wait_seconds}s")

    candidates = _collect_article_urls(entity)
    if not candidates:
        return {
            "status": "no_match",
            "entity": entity,
            "message": f"未在深圳新闻网找到 '{entity}' 相关稿件链接，请尝试更通用的关键词或换数据源",
        }
    logger.info(f"[补齐] 收集到 {len(candidates)} 个候选 URL")

    uploaded_paths: list[str] = []
    uploaded_titles: list[str] = []
    content_hashes: dict[str, str] = {}
    skipped_duplicate = 0
    for url in candidates:
        if len(uploaded_paths) + skipped_duplicate >= count:
            break
        html = _http_get(url)
        if not html:
            continue
        article = _extract_article(html, url=url)
        if not article:
            continue
        if entity not in (article["title"] + " " + article["body"][:1500]):
            continue
        meta = _save_article_to_kb(kb_id, url, article, token)
        if not meta:
            continue
        if meta.get("_skipped"):
            skipped_duplicate += 1
            continue
        if meta.get("file_path"):
            file_path = meta["file_path"]
            uploaded_paths.append(file_path)
            uploaded_titles.append(article["title"])
            if meta.get("content_hash"):
                content_hashes[file_path] = meta["content_hash"]
        time.sleep(0.5)

    if not uploaded_paths:
        if skipped_duplicate > 0:
            return {
                "status": "all_duplicate",
                "entity": entity,
                "candidates": len(candidates),
                "skipped_duplicate": skipped_duplicate,
                "message": (
                    f"找到 {len(candidates)} 个候选 URL，但全部 {skipped_duplicate} 篇 '{entity}' 相关稿件"
                    f"在知识库中已存在（content_hash 重复）。如果上次入库的实体抽取失败导致图谱不全，"
                    f"建议直接到知识库管理界面对这些已存在文件重新触发入库（或先删除再用本工具重新抓取）。"
                ),
            }
        return {
            "status": "no_upload",
            "entity": entity,
            "candidates": len(candidates),
            "message": f"找到 {len(candidates)} 个候选 URL 但未能上传任何含 '{entity}' 的稿件",
        }

    ingest_result = _trigger_ingest(kb_id, uploaded_paths, token, content_hashes=content_hashes)
    task_id = (ingest_result or {}).get("task_id") if isinstance(ingest_result, dict) else None

    if not task_id:
        return {
            "status": "ingest_submit_failed",
            "entity": entity,
            "kb_id": kb_id,
            "uploaded_count": len(uploaded_paths),
            "sample_titles": uploaded_titles[:5],
            "message": f"已上传 {len(uploaded_paths)} 篇稿件，但触发入库任务失败，请到任务中心手动重试",
        }

    logger.info(f"[补齐] 入库任务已提交 task_id={task_id}，开始轮询（最多 {wait_seconds}s）")
    wait_result = _wait_task_done(task_id, token, wait_seconds=wait_seconds)
    task_status = wait_result.get("status")

    indexed_file_ids: set[str] = set()
    failed_file_ids: set[str] = set()
    task_result = wait_result.get("result") or {}
    if isinstance(task_result, dict):
        items = task_result.get("items") or []
        for idx, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            file_key = it.get("file_id") or it.get("path") or it.get("filename") or f"_unkeyed_{idx}"
            if it.get("status") == "failed" or "error" in it:
                failed_file_ids.add(file_key)
            elif it.get("status") in {"indexed", "done", "success"}:
                indexed_file_ids.add(file_key)
    indexed_file_ids -= failed_file_ids
    indexed_count = len(indexed_file_ids)
    failed_count = len(failed_file_ids)

    base_payload: dict[str, Any] = {
        "entity": entity,
        "kb_id": kb_id,
        "uploaded_count": len(uploaded_paths),
        "skipped_duplicate": skipped_duplicate,
        "task_id": task_id,
        "sample_titles": uploaded_titles[:5],
        "ingest_status": task_status,
        "indexed_count": indexed_count,
        "failed_count": failed_count,
        "progress": wait_result.get("progress"),
        "elapsed_seconds": wait_result.get("elapsed_seconds"),
    }

    if task_status == "success":
        base_payload["status"] = "completed"
        base_payload["message"] = (
            f"已从深圳新闻网抓取并上传 {len(uploaded_paths)} 篇关于 '{entity}' 的公开稿件，"
            f"全部完成入库（成功 {indexed_count} / 失败 {failed_count}），现在再次提问即可看到更新后的图谱。"
        )
    elif task_status == "timeout":
        base_payload["status"] = "timeout"
        base_payload["message"] = (
            f"已上传 {len(uploaded_paths)} 篇稿件并启动入库（task_id={task_id}），"
            f"当前进度 {wait_result.get('progress', 0):.0f}%（{wait_result.get('message', '')}）。"
            f"等待 {wait_seconds}s 仍未结束，任务继续在后台运行，约几分钟后再次提问可看到更新结果。"
        )
    elif task_status in {"failed", "cancelled"}:
        base_payload["status"] = task_status
        base_payload["message"] = (
            f"已上传 {len(uploaded_paths)} 篇稿件，但入库任务{task_status}："
            f"{wait_result.get('error') or wait_result.get('message') or '未知原因'}。请到任务中心查看详情。"
        )
    else:
        base_payload["status"] = task_status or "unknown"
        base_payload["message"] = wait_result.get("message", "入库任务状态未知")

    return base_payload
