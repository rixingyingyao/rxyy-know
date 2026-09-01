"""
MinIO 存储工具函数
简化的存储操作辅助函数
"""

import os
import uuid

from fastapi import UploadFile
from yuxi.utils.upload_utils import read_upload_with_limit

from .client import aupload_file_to_minio


def get_file_size(file_path: str) -> int:
    """获取文件大小"""
    return os.path.getsize(file_path)


def generate_unique_filename(original_name: str) -> str:
    """生成唯一的文件名"""
    name_parts = original_name.rsplit(".", 1)
    base_name = name_parts[0] if len(name_parts) == 2 else original_name
    extension = f".{name_parts[1]}" if len(name_parts) == 2 else ""
    return f"{base_name}_{uuid.uuid4().hex[:8]}{extension}"


async def upload_image_to_minio(
    upload: UploadFile,
    *,
    object_prefix: str,
    max_size_bytes: int,
    too_large_message: str,
) -> str:
    if not upload.content_type or not upload.content_type.startswith("image/"):
        raise ValueError("只能上传图片文件")

    file_content = await read_upload_with_limit(
        upload,
        max_size_bytes=max_size_bytes,
        too_large_message=too_large_message,
    )
    file_extension = upload.filename.rsplit(".", 1)[-1].lower() if upload.filename and "." in upload.filename else "jpg"
    object_name = f"{object_prefix.strip('/')}/{uuid.uuid4()}.{file_extension}"
    return await aupload_file_to_minio("public", object_name, file_content)
