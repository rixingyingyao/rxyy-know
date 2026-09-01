import io
import os
import zipfile
from pathlib import Path

import aiofiles
from fastapi import UploadFile

# rxyy-know 扩展：媒资场景视频文件常超 100MB，上限可通过 env 覆盖（默认 500MB）
MAX_UPLOAD_SIZE_BYTES = int(os.getenv("YUXI_MAX_UPLOAD_MB", "500")) * 1024 * 1024

# OOXML 文档本质是 zip；损坏文件（如中央目录残缺）会在解析阶段才失败并卡 error_parsing。
# rxyy-know 扩展：上传时预检 zip 结构，坏文件立即 400 拒收。
OOXML_EXTENSIONS = (".docx", ".xlsx", ".pptx")


def validate_ooxml_bytes(filename: str, file_bytes: bytes) -> None:
    """校验 docx/xlsx/pptx 的 zip 结构完整性，损坏时抛 ValueError。"""
    if Path(filename).suffix.lower() not in OOXML_EXTENSIONS:
        return
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            if zf.testzip() is not None:
                raise ValueError("压缩条目 CRC 校验失败")
    except ValueError:
        raise ValueError(f"文件已损坏（Office 文档 zip 结构不完整），请换源文件后重试: {filename}")
    except zipfile.BadZipFile as e:
        raise ValueError(f"文件已损坏（Office 文档 zip 结构不完整），请换源文件后重试: {filename} ({e})")


async def write_upload_to_buffer(
    upload: UploadFile,
    buffer,
    *,
    max_size_bytes: int,
    too_large_message: str,
    chunk_size: int = 1024 * 1024,
) -> int:
    await upload.seek(0)
    written = 0

    while chunk := await upload.read(chunk_size):
        written += len(chunk)
        if written > max_size_bytes:
            raise ValueError(too_large_message)
        await buffer.write(chunk)

    return written


async def read_upload_with_limit(
    upload: UploadFile,
    *,
    max_size_bytes: int,
    too_large_message: str,
    chunk_size: int = 1024 * 1024,
) -> bytes:
    await upload.seek(0)
    written = 0
    chunks: list[bytes] = []

    while chunk := await upload.read(chunk_size):
        written += len(chunk)
        if written > max_size_bytes:
            raise ValueError(too_large_message)
        chunks.append(chunk)

    return b"".join(chunks)


async def write_upload_to_path(
    upload: UploadFile,
    dest: Path,
    *,
    max_size_bytes: int,
    too_large_message: str,
    mode: str = "wb",
    chunk_size: int = 1024 * 1024,
) -> int:
    async with aiofiles.open(dest, mode) as buffer:
        return await write_upload_to_buffer(
            upload,
            buffer,
            max_size_bytes=max_size_bytes,
            too_large_message=too_large_message,
            chunk_size=chunk_size,
        )
