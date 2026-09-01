"""
Qwen-OCR Parser（rxyy-know 扩展）

使用阿里百炼 qwen3.5-ocr（DashScope OpenAI 兼容接口）做文档/图片文字提取。
qwen3.5-ocr 基于 Qwen3.5 架构：128K 上下文、最大输出 32K token，
支持文档解析、表格、公式与多语言识别，PDF 按页转图逐页识别。
"""

import base64
import io
import os
import time
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import requests

from yuxi.knowledge.parser.base import BaseDocumentProcessor, DocumentParserException
from yuxi.utils import logger

_DEFAULT_PROMPT = "请提取图中的全部文字内容，按原有版面顺序输出为 Markdown（表格用 Markdown 表格表示），不要添加任何解释。"

# DashScope qwen-vl/ocr 的两道限制取更严者：
#   ① 整个请求 JSON 字符串 ≤28MB（StreamReadConstraints）
#   ② 单个 data-uri 图片 base64 后 ≤10MB（"max bytes per data-uri"）
# 以 ② 为准：base64 放大 ~4/3，留余量后目标 base64 长度 ≤9.2MB，对应原始 JPEG 字节 ≤6.9MB。
_MAX_DATAURI_B64 = 9_200_000  # base64 后目标上限（<10MB data-uri 限制）
_MAX_RAW_BYTES = int(_MAX_DATAURI_B64 * 3 / 4)  # ~6.9MB 原始字节
_MAX_LONG_EDGE = 3000  # 长边像素上限，OCR 3000px 足够清晰

# rxyy-know 扩展：PDF 页自带文本层且字符数达阈值时直读文本层（零 API 成本），仅扫描页走云端 OCR。
# 电子版手册/报告类 PDF 可省掉几乎全部 OCR 调用；QWEN_OCR_TEXT_LAYER_FIRST=0 可关闭回到全页 OCR。
_TEXT_LAYER_FIRST = os.getenv("QWEN_OCR_TEXT_LAYER_FIRST", "1") not in ("0", "false", "False")
_TEXT_LAYER_MIN_CHARS = 100


def _b64len(n: int) -> int:
    """n 字节 base64 编码后的长度。"""
    return ((n + 2) // 3) * 4


def _shrink_image_if_needed(data_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """大图等比降采样并转 JPEG，保证 base64 后不超过 DashScope data-uri 上限。

    小图原样返回。依赖 Pillow；不可用或处理失败时回退原图（由上层 400 兜底）。
    """
    if _b64len(len(data_bytes)) <= _MAX_DATAURI_B64:
        return data_bytes, mime_type
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data_bytes))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        long_edge = max(img.size)
        if long_edge > _MAX_LONG_EDGE:
            scale = _MAX_LONG_EDGE / long_edge
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
        quality = 85
        while True:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            out = buf.getvalue()
            if _b64len(len(out)) <= _MAX_DATAURI_B64 or quality <= 35:
                # 质量已到底仍超限，再按比例缩尺寸兜底一轮
                if _b64len(len(out)) > _MAX_DATAURI_B64 and min(img.size) > 800:
                    img = img.resize((max(1, int(img.width * 0.8)), max(1, int(img.height * 0.8))), Image.LANCZOS)
                    quality = 80
                    continue
                logger.info(
                    f"Qwen-OCR shrank oversized image {len(data_bytes)}B -> {len(out)}B "
                    f"(q={quality}, size={img.size})"
                )
                return out, "image/jpeg"
            quality -= 10
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Qwen-OCR image shrink failed ({e}); sending original bytes")
        return data_bytes, mime_type


class QwenOCRParser(BaseDocumentProcessor):
    """阿里百炼 Qwen-OCR 文字提取（qwen3.5-ocr）"""

    MIME_TYPE_MAP = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise DocumentParserException(
                "DASHSCOPE_API_KEY environment variable not set", "qwen_ocr", "missing_api_key"
            )

        self.api_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        self.model = model or os.getenv("QWEN_OCR_MODEL", "qwen3.5-ocr")
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def get_service_name(self) -> str:
        return "qwen_ocr"

    def get_supported_extensions(self) -> list[str]:
        return list(self.MIME_TYPE_MAP.keys())

    def check_health(self) -> dict[str, Any]:
        try:
            response = requests.get(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
                headers=self.headers,
                timeout=10,
            )
            if response.status_code == 200:
                return {
                    "status": "healthy",
                    "message": f"Qwen-OCR (DashScope, {self.model}) is available",
                    "details": {"api_url": self.api_url, "model": self.model},
                }
            if response.status_code == 401:
                return {"status": "unhealthy", "message": "Invalid API Key", "details": {"error_code": "401"}}
            return {
                "status": "unhealthy",
                "message": f"API Error: {response.status_code}",
                "details": {"status_code": response.status_code},
            }
        except Exception as e:  # noqa: BLE001
            return {"status": "unavailable", "message": f"Connection failed: {str(e)}", "details": {"error": str(e)}}

    def process_file(self, file_path: str, params: dict[str, Any] | None = None) -> str:
        if not os.path.exists(file_path):
            raise DocumentParserException(f"File not found: {file_path}", self.get_service_name(), "file_not_found")

        file_ext = Path(file_path).suffix.lower()
        if not self.supports_file_type(file_ext):
            raise DocumentParserException(
                f"Unsupported file type: {file_ext}", self.get_service_name(), "unsupported_file_type"
            )

        try:
            start_time = time.time()
            logger.info(f"Qwen-OCR starting: {os.path.basename(file_path)} ({self.model})")

            if file_ext == ".pdf":
                content = self._process_pdf(file_path)
            else:
                mime_type = self.MIME_TYPE_MAP.get(file_ext, "image/jpeg")
                with open(file_path, "rb") as f:
                    content = self._call_api(f.read(), mime_type)

            elapsed = time.time() - start_time
            logger.info(f"Qwen-OCR finished: {os.path.basename(file_path)} - {len(content)} chars ({elapsed:.2f}s)")
            return content
        except Exception as e:
            if isinstance(e, DocumentParserException):
                raise
            error_msg = f"Qwen-OCR failed: {str(e)}"
            logger.error(error_msg)
            raise DocumentParserException(error_msg, self.get_service_name(), "processing_failed")

    def _process_pdf(self, file_path: str) -> str:
        doc = fitz.open(file_path)
        try:
            total_pages = len(doc)
            logger.info(f"Qwen-OCR processing PDF with {total_pages} pages")
            full_text = []
            text_layer_pages = 0
            for i, page in enumerate(doc):
                if _TEXT_LAYER_FIRST:
                    page_text = (page.get_text() or "").strip()
                    if len(page_text) >= _TEXT_LAYER_MIN_CHARS:
                        full_text.append(page_text)
                        text_layer_pages += 1
                        continue
                logger.debug(f"Qwen-OCR page {i + 1}/{total_pages}")
                pix = page.get_pixmap(dpi=200)
                full_text.append(self._call_api(pix.tobytes("png"), "image/png"))
            if text_layer_pages:
                logger.info(
                    f"Qwen-OCR text-layer fast path: {text_layer_pages}/{total_pages} pages "
                    "read from PDF text layer (no OCR API calls)"
                )
            return "\n\n".join(full_text)
        finally:
            doc.close()

    def _call_api(self, data_bytes: bytes, mime_type: str) -> str:
        data_bytes, mime_type = _shrink_image_if_needed(data_bytes, mime_type)
        data_url = f"data:{mime_type};base64,{base64.b64encode(data_bytes).decode('utf-8')}"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": _DEFAULT_PROMPT},
                    ],
                }
            ],
            "temperature": 0.0,
        }

        response = requests.post(self.api_url, headers=self.headers, json=payload, timeout=180)
        if response.status_code != 200:
            error_msg = f"API Error {response.status_code}: {response.text[:500]}"
            logger.error(error_msg)
            raise DocumentParserException(error_msg, self.get_service_name(), f"http_{response.status_code}")

        result = response.json()
        return (result["choices"][0]["message"]["content"] or "").strip()
