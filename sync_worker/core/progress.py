from __future__ import annotations

import os
import time

from aiogram.types import FSInputFile

from ..runtime import sync_state


UPLOAD_LABELS = {
    "photo": "上传图片",
    "video": "上传视频",
    "animation": "上传动图",
    "audio": "上传音频",
    "voice": "上传语音",
    "sticker": "上传贴纸",
    "document": "上传文件",
}


class UploadProgressTracker:
    def __init__(self, label: str, total_bytes: int):
        self.label = label
        self.total_bytes = max(1, int(total_bytes or 0))
        self.started_at = time.time()
        self.last_update_at = 0.0
        self._sent_bytes = 0

    def _render(self, sent_bytes: int, file_label: str) -> None:
        sent_bytes = max(0, min(int(sent_bytes or 0), self.total_bytes))
        now = time.time()
        if sent_bytes < self.total_bytes and now - self.last_update_at < 0.2:
            return
        elapsed = max(0.001, now - self.started_at)
        percent = min(100.0, sent_bytes / self.total_bytes * 100)
        sent_mb = sent_bytes / (1024 * 1024)
        total_mb = self.total_bytes / (1024 * 1024)
        speed_mb = sent_mb / elapsed
        sync_state["current_text"] = (
            f"{file_label}\n{self.label} {percent:.1f}% ({sent_mb:.1f}/{total_mb:.1f} MB, {speed_mb:.1f} MB/s)"
        )
        self.last_update_at = now

    def advance(self, chunk_size: int, file_label: str) -> None:
        self._sent_bytes = min(self.total_bytes, self._sent_bytes + max(0, int(chunk_size or 0)))
        self._render(self._sent_bytes, file_label)

    def set_absolute(self, sent_bytes: int, file_label: str, total_bytes: int | None = None) -> None:
        if total_bytes:
            self.total_bytes = max(1, int(total_bytes))
        self._sent_bytes = max(0, min(int(sent_bytes or 0), self.total_bytes))
        self._render(self._sent_bytes, file_label)


class ProgressFSInputFile(FSInputFile):
    def __init__(self, path: str, tracker: UploadProgressTracker, file_label: str, filename: str | None = None):
        super().__init__(path, filename=filename)
        self.tracker = tracker
        self.file_label = file_label

    async def read(self, bot):
        async for chunk in super().read(bot):
            self.tracker.advance(len(chunk), self.file_label)
            yield chunk


def format_upload_label(msg_type: str | None, media_path: str, *, index: int | None = None, total: int | None = None) -> str:
    action = UPLOAD_LABELS.get(str(msg_type or ""), "上传媒体")
    name = os.path.basename(media_path)
    if index is not None and total is not None:
        return f"{action} {index}/{total}: {name}"
    return f"{action}: {name}"


def build_pyro_progress_callback(
    tracker: UploadProgressTracker,
    file_label: str,
    *,
    total_bytes: int | None = None,
    client=None,
):
    def _callback(current: int, total: int, *args):
        if sync_state.get("stop_requested") and client is not None and hasattr(client, "stop_transmission"):
            try:
                client.stop_transmission()
            except Exception:
                pass
            return
        tracker.set_absolute(current, file_label, total_bytes or total)

    return _callback
