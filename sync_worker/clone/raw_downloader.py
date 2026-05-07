from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass

from pyrogram import raw
from pyrogram.file_id import FileId
from pyrogram.session.auth import Auth
from pyrogram.session.session import Session

from services.sync_services import create_progress_callback

from ..runtime import sync_state


CHUNK_SIZE = 1024 * 1024
MAX_WORKERS = 8
CHUNK_RETRY_ATTEMPTS = 2
SUPPORTED_CHUNK_MEDIA_TYPES = {"video", "animation", "document"}


class ChunkedDownloadFallback(RuntimeError):
    pass


def _is_limit_invalid_error(exc: Exception) -> bool:
    text = str(exc or "").upper()
    return "LIMIT_INVALID" in text or "LIMIT PARAMETER IS INVALID" in text


def _is_auth_bytes_invalid_error(exc: Exception) -> bool:
    text = str(exc or "").upper()
    return "AUTH_BYTES_INVALID" in text


@dataclass(slots=True)
class ChunkDownloadRequest:
    dc_id: int
    location: object
    file_size: int
    file_name: str


def _resolve_media_obj(msg, msg_type: str):
    return getattr(msg, msg_type, None)


def supports_chunked_download(msg_type: str) -> bool:
    return str(msg_type or "").strip() in SUPPORTED_CHUNK_MEDIA_TYPES


def build_chunk_download_request(msg, msg_type: str) -> ChunkDownloadRequest:
    media_obj = _resolve_media_obj(msg, msg_type)
    if media_obj is None:
        raise ChunkedDownloadFallback(f"消息类型 {msg_type} 缺少媒体对象")

    file_id = str(getattr(media_obj, "file_id", "") or "").strip()
    file_size = int(getattr(media_obj, "file_size", 0) or 0)
    file_name = str(getattr(media_obj, "file_name", "") or "").strip()

    if not file_id:
        raise ChunkedDownloadFallback("媒体缺少 file_id")
    if file_size <= 0:
        raise ChunkedDownloadFallback("媒体缺少有效文件大小")

    try:
        decoded = FileId.decode(file_id)
    except Exception as exc:
        raise ChunkedDownloadFallback(f"file_id 解析失败: {exc}") from exc

    thumb_size = getattr(decoded, "thumbnail_size", "") or ""

    if msg_type == "photo":
        location = raw.types.InputPhotoFileLocation(
            id=decoded.media_id,
            access_hash=decoded.access_hash,
            file_reference=decoded.file_reference,
            thumb_size=thumb_size,
        )
    elif supports_chunked_download(msg_type):
        location = raw.types.InputDocumentFileLocation(
            id=decoded.media_id,
            access_hash=decoded.access_hash,
            file_reference=decoded.file_reference,
            thumb_size=thumb_size,
        )
    else:
        raise ChunkedDownloadFallback(f"媒体类型 {msg_type} 不支持分块并发下载")

    return ChunkDownloadRequest(
        dc_id=int(decoded.dc_id),
        location=location,
        file_size=file_size,
        file_name=file_name,
    )


async def _create_media_session(app, dc_id: int) -> Session:
    base_dc_id = await app.storage.dc_id()
    test_mode = await app.storage.test_mode()
    auth_key = await Auth(app, dc_id, test_mode).create() if dc_id != base_dc_id else await app.storage.auth_key()
    session = Session(app, dc_id, auth_key, test_mode, is_media=True)
    await session.start()
    if dc_id != base_dc_id:
        try:
            exported_auth = await app.invoke(raw.functions.auth.ExportAuthorization(dc_id=dc_id))
            await session.invoke(
                raw.functions.auth.ImportAuthorization(
                    id=exported_auth.id,
                    bytes=exported_auth.bytes,
                )
            )
        except Exception as exc:
            if _is_auth_bytes_invalid_error(exc):
                raise ChunkedDownloadFallback("跨 DC 分块授权失败，已回退普通下载") from exc
            raise
    return session


async def _writer_loop(result_queue: asyncio.Queue, file_path: str, file_size: int, total_parts: int, progress_label: str) -> None:
    progress = create_progress_callback(progress_label, sync_state)
    completed = 0
    downloaded = 0

    with open(file_path, "w+b") as file_obj:
        file_obj.truncate(file_size)
        while completed < total_parts:
            item = await result_queue.get()
            offset, chunk = item
            file_obj.seek(offset)
            file_obj.write(chunk)
            file_obj.flush()
            completed += 1
            downloaded += len(chunk)
            progress(min(downloaded, file_size), file_size)


async def _download_chunk(session: Session, location, offset: int, limit: int):
    try:
        response = await session.invoke(
            raw.functions.upload.GetFile(
                location=location,
                offset=offset,
                limit=limit,
            ),
            sleep_threshold=30,
        )
    except Exception as exc:
        if _is_limit_invalid_error(exc):
            raise ChunkedDownloadFallback("当前文件不支持分块下载，已回退普通下载") from exc
        raise
    if isinstance(response, raw.types.upload.FileCdnRedirect):
        raise ChunkedDownloadFallback("媒体下载命中 Telegram CDN，已回退普通下载")
    if not isinstance(response, raw.types.upload.File):
        raise RuntimeError(f"未知下载响应类型: {type(response).__name__}")
    return bytes(response.bytes)


async def download_media_in_chunks(
    app,
    msg,
    msg_type: str,
    file_path: str,
    worker_count: int = 4,
    *,
    progress_label: str = "下载中",
) -> str:
    request = build_chunk_download_request(msg, msg_type)
    total_parts = max(1, math.ceil(request.file_size / CHUNK_SIZE))
    worker_count = min(MAX_WORKERS, max(1, int(worker_count or 1)), total_parts)
    result_queue: asyncio.Queue = asyncio.Queue()
    part_queue: asyncio.Queue = asyncio.Queue()
    sessions: list[Session] = []
    worker_tasks: list[asyncio.Task] = []

    for part_index in range(total_parts):
        part_queue.put_nowait(part_index)

    writer_task = asyncio.create_task(_writer_loop(result_queue, file_path, request.file_size, total_parts, progress_label))

    async def worker_loop(session: Session) -> None:
        while True:
            if sync_state.get("stop_requested"):
                raise RuntimeError("STOP_REQUESTED")
            try:
                part_index = part_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            offset = part_index * CHUNK_SIZE
            limit = min(CHUNK_SIZE, request.file_size - offset)
            try:
                last_exc = None
                for _ in range(CHUNK_RETRY_ATTEMPTS):
                    try:
                        chunk = await _download_chunk(session, request.location, offset, limit)
                        await result_queue.put((offset, chunk))
                        last_exc = None
                        break
                    except ChunkedDownloadFallback:
                        raise
                    except Exception as exc:  # pragma: no cover - exercised by coordinator tests with retry exhaustion
                        if sync_state.get("stop_requested"):
                            raise RuntimeError("STOP_REQUESTED") from exc
                        last_exc = exc
                        await asyncio.sleep(0.2)
                if last_exc is not None:
                    raise last_exc
            finally:
                part_queue.task_done()

    try:
        for _ in range(worker_count):
            sessions.append(await _create_media_session(app, request.dc_id))
        worker_tasks = [asyncio.create_task(worker_loop(session)) for session in sessions]
        await asyncio.gather(*worker_tasks)
        await writer_task
        return file_path
    except Exception:
        writer_task.cancel()
        await asyncio.gather(writer_task, return_exceptions=True)
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        try:
            os.remove(file_path)
        except Exception:
            pass
        raise
    finally:
        for session in sessions:
            try:
                await session.stop()
            except Exception:
                pass
