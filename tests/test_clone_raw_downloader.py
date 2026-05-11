import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pyrogram import raw
from pyrogram.file_id import FileId

from sync_worker.clone.raw_downloader import (
    CHUNK_SIZE,
    ChunkDownloadRequest,
    ChunkedDownloadFallback,
    build_chunk_download_request,
    download_media_in_chunks,
)


class FakeStorage:
    async def dc_id(self):
        return 1

    async def test_mode(self):
        return False


class FakeSession:
    def __init__(self):
        self.stop = AsyncMock()


class RawDownloaderTests(unittest.IsolatedAsyncioTestCase):
    def test_build_chunk_download_request_supports_photo(self):
        msg = SimpleNamespace(photo=SimpleNamespace(file_id="photo-file-id", file_size=789))

        with patch(
            "sync_worker.clone.raw_downloader.FileId.decode",
            return_value=SimpleNamespace(
                dc_id=4,
                media_id=123,
                access_hash=456,
                file_reference=b"ref",
                thumbnail_size="y",
            ),
        ):
            request = build_chunk_download_request(msg, "photo")

        self.assertEqual(request.dc_id, 4)
        self.assertEqual(request.file_size, 789)
        self.assertIsInstance(request.location, raw.types.InputPhotoFileLocation)
        self.assertEqual(request.location.id, 123)

    def test_build_chunk_download_request_supports_document_family(self):
        file_id = FileId(
            file_type=5,
            dc_id=6,
            file_reference=b"doc-ref",
            media_id=321,
            access_hash=654,
            thumbnail_size="",
        ).encode()
        msg = SimpleNamespace(
            document=SimpleNamespace(file_id=file_id, file_size=2048, file_name="demo.bin"),
        )

        request = build_chunk_download_request(msg, "document")

        self.assertEqual(request.dc_id, 6)
        self.assertEqual(request.file_size, 2048)
        self.assertIsInstance(request.location, raw.types.InputDocumentFileLocation)
        self.assertEqual(request.location.id, 321)

    async def test_download_media_in_chunks_writes_all_parts_once(self):
        request = ChunkDownloadRequest(dc_id=1, location=object(), file_size=CHUNK_SIZE * 2 + 5, file_name="demo.bin")
        offsets = []
        sessions = [FakeSession(), FakeSession(), FakeSession()]

        async def fake_download_chunk(_session, _location, offset, limit):
            offsets.append(offset)
            if offset == 0:
                return b"A" * limit
            if offset == CHUNK_SIZE:
                return b"B" * limit
            return b"C" * limit

        app = SimpleNamespace(storage=FakeStorage(), invoke=AsyncMock())
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("sync_worker.clone.raw_downloader.build_chunk_download_request", return_value=request), \
             patch("sync_worker.clone.raw_downloader._create_media_session", AsyncMock(side_effect=sessions)), \
             patch("sync_worker.clone.raw_downloader._download_chunk", side_effect=fake_download_chunk):
            output = Path(temp_dir) / "out.bin"
            result = await download_media_in_chunks(app, object(), "document", str(output), worker_count=5)

            self.assertEqual(result, str(output))
            self.assertEqual(output.stat().st_size, request.file_size)
            self.assertEqual(output.read_bytes(), (b"A" * CHUNK_SIZE) + (b"B" * CHUNK_SIZE) + (b"C" * 5))

        self.assertEqual(sorted(offsets), [0, CHUNK_SIZE, CHUNK_SIZE * 2])
        self.assertEqual(app.invoke.await_count, 0)

    async def test_download_media_in_chunks_removes_partial_file_on_fallback(self):
        request = ChunkDownloadRequest(dc_id=1, location=object(), file_size=CHUNK_SIZE + 10, file_name="demo.bin")
        app = SimpleNamespace(storage=FakeStorage(), invoke=AsyncMock())

        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("sync_worker.clone.raw_downloader.build_chunk_download_request", return_value=request), \
             patch("sync_worker.clone.raw_downloader._create_media_session", AsyncMock(return_value=FakeSession())), \
             patch("sync_worker.clone.raw_downloader._download_chunk", side_effect=ChunkedDownloadFallback("命中 CDN")):
            output = Path(temp_dir) / "out.bin"
            with self.assertRaises(ChunkedDownloadFallback):
                await download_media_in_chunks(app, object(), "document", str(output), worker_count=2)
            self.assertFalse(output.exists())

    async def test_download_chunk_turns_limit_invalid_into_fallback(self):
        session = SimpleNamespace(
            invoke=AsyncMock(side_effect=RuntimeError('Telegram says: [400 LIMIT_INVALID] (caused by "upload.GetFile")'))
        )

        from sync_worker.clone.raw_downloader import _download_chunk

        with self.assertRaises(ChunkedDownloadFallback):
            await _download_chunk(session, object(), 0, CHUNK_SIZE)

    async def test_create_media_session_turns_auth_bytes_invalid_into_fallback(self):
        app = SimpleNamespace(
            storage=FakeStorage(),
            invoke=AsyncMock(return_value=SimpleNamespace(id=1, bytes=b"bad")),
        )
        fake_session = FakeSession()
        fake_session.start = AsyncMock()
        fake_session.invoke = AsyncMock(side_effect=RuntimeError('Telegram says: [400 AUTH_BYTES_INVALID] (caused by "auth.ImportAuthorization")'))
        fake_auth = SimpleNamespace(create=AsyncMock(return_value="auth-key"))

        from sync_worker.clone.raw_downloader import _create_media_session

        with patch("sync_worker.clone.raw_downloader.Auth", return_value=fake_auth), \
             patch("sync_worker.clone.raw_downloader.Session", return_value=fake_session):
            with self.assertRaises(ChunkedDownloadFallback):
                await _create_media_session(app, 2)
