from __future__ import annotations

import os
import shutil

import database as db

from .hash_perturb import perturb_clone_media


def describe_hash_perturb_reason(reason: str) -> str:
    if reason == "disabled":
        return "未启用指纹重置"
    if reason == "unsupported_type":
        return "当前类型不支持处理"
    if reason == "tail_bytes_appended":
        return "已在文件尾部追加随机字节"
    if reason.startswith("append_error:"):
        detail = reason.split(":", 1)[1].strip()
        return f"追加尾部字节失败: {detail or '未知错误'}"
    return reason or "未知状态"


async def prepare_media_for_send(
    file_path: str,
    msg_type: str,
    msg_id: int,
    enabled: bool,
    *,
    preserve_original: bool = False,
    temp_dir: str | None = None,
) -> str:
    if msg_type not in {"photo", "video"}:
        return file_path

    if not enabled:
        await db.add_msg_log("HASH_PERTURB_SKIP", f"消息ID:{msg_id} | 类型:{msg_type} | {describe_hash_perturb_reason('disabled')}")
        return file_path

    working_path = file_path
    if preserve_original:
        if not temp_dir:
            raise ValueError("preserve_original=True 时必须提供 temp_dir")
        safe_name = os.path.basename(file_path)
        working_path = os.path.join(temp_dir, f"{msg_id}_{safe_name}")
        shutil.copy2(file_path, working_path)

    result = perturb_clone_media(working_path, msg_type)
    if result.changed:
        await db.add_msg_log("HASH_PERTURB_OK", f"消息ID:{msg_id} | 类型:{msg_type} | {describe_hash_perturb_reason(result.reason)}")
    else:
        await db.add_msg_log("HASH_PERTURB_SKIP", f"消息ID:{msg_id} | 类型:{msg_type} | {describe_hash_perturb_reason(result.reason)}")
    return result.path


async def prepare_json_media_for_send(file_path: str, msg_type: str, msg_id: int, enabled: bool, *, temp_dir: str) -> tuple[str, bool]:
    if not enabled or msg_type not in {"photo", "video"}:
        return file_path, False
    prepared_path = await prepare_media_for_send(
        file_path,
        msg_type,
        msg_id,
        enabled,
        preserve_original=True,
        temp_dir=temp_dir,
    )
    return prepared_path, prepared_path != file_path
