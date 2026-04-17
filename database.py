from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

import aiosqlite

from app_paths import database_file, ensure_runtime_dirs


DB_FILE = str(database_file())
LOG_RETENTION_LIMIT = 100
_db_conn: aiosqlite.Connection | None = None
_db_lock = asyncio.Lock()


async def _configure_connection(conn: aiosqlite.Connection) -> aiosqlite.Connection:
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute("PRAGMA synchronous=NORMAL")
    return conn


async def _get_connection() -> aiosqlite.Connection:
    global _db_conn
    ensure_runtime_dirs()
    async with _db_lock:
        if _db_conn is None:
            _db_conn = await aiosqlite.connect(DB_FILE)
            await _configure_connection(_db_conn)
    return _db_conn


async def close_db() -> None:
    global _db_conn
    async with _db_lock:
        if _db_conn is not None:
            await _db_conn.close()
            _db_conn = None


async def _run_in_db(
    action: Callable[[aiosqlite.Connection], Awaitable[None | object]],
    *,
    commit: bool = False,
):
    conn = await _get_connection()
    async with _db_lock:
        result = await action(conn)
        if commit:
            await conn.commit()
        return result


async def _fetchall(sql: str, params: tuple = ()) -> list:
    async def action(conn: aiosqlite.Connection):
        cursor = await conn.execute(sql, params)
        return await cursor.fetchall()

    return await _run_in_db(action)


async def _fetchone(sql: str, params: tuple = ()):
    async def action(conn: aiosqlite.Connection):
        cursor = await conn.execute(sql, params)
        return await cursor.fetchone()

    return await _run_in_db(action)


async def _execute(sql: str, params: tuple = (), *, commit: bool = False):
    async def action(conn: aiosqlite.Connection):
        await conn.execute(sql, params)

    return await _run_in_db(action, commit=commit)


async def _executemany(sql: str, rows: list[tuple], *, commit: bool = False):
    async def action(conn: aiosqlite.Connection):
        await conn.executemany(sql, rows)

    return await _run_in_db(action, commit=commit)


async def _migrate_channel_mappings(conn: aiosqlite.Connection) -> None:
    old_channel_rows = []
    old_channel_sql_row = await (
        await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'channel_mappings'"
        )
    ).fetchone()
    if old_channel_sql_row and "source_id INTEGER NOT NULL UNIQUE" in (old_channel_sql_row[0] or ""):
        old_channel_rows = await (await conn.execute("SELECT source_id, target_id FROM channel_mappings")).fetchall()
        await conn.execute("ALTER TABLE channel_mappings RENAME TO channel_mappings_old")

    await conn.execute(
        "CREATE TABLE IF NOT EXISTS channel_mappings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source_id INTEGER NOT NULL, "
        "target_id INTEGER NOT NULL, "
        "UNIQUE(source_id, target_id))"
    )
    if old_channel_rows:
        await conn.executemany(
            "INSERT OR IGNORE INTO channel_mappings (source_id, target_id) VALUES (?, ?)",
            old_channel_rows,
        )
        await conn.execute("DROP TABLE channel_mappings_old")


async def _migrate_message_mappings(conn: aiosqlite.Connection) -> None:
    current_channel_rows = await (await conn.execute("SELECT source_id, target_id FROM channel_mappings")).fetchall()
    old_channel_map = {row[0]: row[1] for row in current_channel_rows}
    old_message_rows = []
    old_message_sql_row = await (
        await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'message_mappings'"
        )
    ).fetchone()
    if old_message_sql_row and "target_channel_id" not in (old_message_sql_row[0] or ""):
        old_message_rows = await (
            await conn.execute("SELECT source_channel_id, source_msg_id, target_msg_id FROM message_mappings")
        ).fetchall()
        await conn.execute("ALTER TABLE message_mappings RENAME TO message_mappings_old")

    await conn.execute(
        "CREATE TABLE IF NOT EXISTS message_mappings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source_channel_id INTEGER NOT NULL, "
        "source_msg_id INTEGER NOT NULL, "
        "target_channel_id INTEGER NOT NULL, "
        "target_msg_id INTEGER NOT NULL, "
        "UNIQUE(source_channel_id, source_msg_id, target_channel_id))"
    )
    if old_message_rows:
        migrated_rows = [
            (source_channel_id, source_msg_id, old_channel_map[source_channel_id], target_msg_id)
            for source_channel_id, source_msg_id, target_msg_id in old_message_rows
            if source_channel_id in old_channel_map
        ]
        if migrated_rows:
            await conn.executemany(
                "INSERT OR IGNORE INTO message_mappings "
                "(source_channel_id, source_msg_id, target_channel_id, target_msg_id) VALUES (?, ?, ?, ?)",
                migrated_rows,
            )
        await conn.execute("DROP TABLE message_mappings_old")


async def _ensure_supporting_tables(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS system_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "level TEXT NOT NULL, "
        "message TEXT NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS filter_rules ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "rule_type TEXT NOT NULL, "
        "pattern TEXT NOT NULL, "
        "replacement TEXT, "
        "is_case_sensitive INTEGER DEFAULT 0)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS message_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "action TEXT NOT NULL, "
        "detail TEXT NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS global_settings ("
        "setting_key TEXT PRIMARY KEY, "
        "setting_value TEXT NOT NULL)"
    )
    try:
        await conn.execute("ALTER TABLE filter_rules ADD COLUMN is_case_sensitive INTEGER DEFAULT 0")
    except Exception:
        pass
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_source ON channel_mappings(source_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_target ON channel_mappings(target_id)")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_msg_target "
        "ON message_mappings(source_channel_id, source_msg_id, target_channel_id)"
    )


async def _seed_default_settings(conn: aiosqlite.Connection) -> None:
    default_settings = {
        "sync_text": "1",
        "sync_photo": "1",
        "sync_video": "1",
        "sync_document": "1",
        "sync_sticker": "1",
        "sync_gif": "1",
        "sync_audio": "1",
        "sync_voice": "1",
    }
    for key, value in default_settings.items():
        await conn.execute(
            "INSERT OR IGNORE INTO global_settings (setting_key, setting_value) VALUES (?, ?)",
            (key, value),
        )


async def init_db():
    conn = await _get_connection()
    async with _db_lock:
        await _migrate_channel_mappings(conn)
        await _migrate_message_mappings(conn)
        await _ensure_supporting_tables(conn)
        await _seed_default_settings(conn)
        await conn.commit()


async def add_channel_mapping(source_id: int, target_id: int):
    await _execute(
        "INSERT OR IGNORE INTO channel_mappings (source_id, target_id) VALUES (?, ?)",
        (source_id, target_id),
        commit=True,
    )


async def delete_channel_mapping(source_id: int, target_id: int | None = None):
    if target_id is None:
        await _execute("DELETE FROM channel_mappings WHERE source_id = ?", (source_id,), commit=True)
        return
    await _execute(
        "DELETE FROM channel_mappings WHERE source_id = ? AND target_id = ?",
        (source_id, target_id),
        commit=True,
    )


async def get_target_channels(source_id: int) -> list[int]:
    rows = await _fetchall("SELECT target_id FROM channel_mappings WHERE source_id = ? ORDER BY target_id", (source_id,))
    return [row[0] for row in rows]


async def get_all_channel_mappings() -> list:
    return await _fetchall("SELECT source_id, target_id FROM channel_mappings ORDER BY target_id, source_id")


async def save_msg_mapping(
    source_channel_id: int,
    source_msg_id: int,
    target_channel_id: int,
    target_msg_id: int,
    overwrite: bool = False,
):
    sql = (
        "INSERT OR REPLACE INTO message_mappings "
        "(source_channel_id, source_msg_id, target_channel_id, target_msg_id) VALUES (?, ?, ?, ?)"
        if overwrite
        else
        "INSERT OR IGNORE INTO message_mappings "
        "(source_channel_id, source_msg_id, target_channel_id, target_msg_id) VALUES (?, ?, ?, ?)"
    )
    await _execute(sql, (source_channel_id, source_msg_id, target_channel_id, target_msg_id), commit=True)


async def get_target_msg_id(source_channel_id: int, source_msg_id: int, target_channel_id: int) -> int | None:
    row = await _fetchone(
        "SELECT target_msg_id FROM message_mappings "
        "WHERE source_channel_id = ? AND source_msg_id = ? AND target_channel_id = ?",
        (source_channel_id, source_msg_id, target_channel_id),
    )
    return row[0] if row else None


async def get_all_target_msg_mappings(source_channel_id: int, source_msg_id: int) -> list[tuple[int, int]]:
    return await _fetchall(
        "SELECT target_channel_id, target_msg_id FROM message_mappings "
        "WHERE source_channel_id = ? AND source_msg_id = ? ORDER BY target_channel_id",
        (source_channel_id, source_msg_id),
    )


async def is_message_synced(source_channel_id: int, source_msg_id: int, target_channel_id: int) -> bool:
    row = await _fetchone(
        "SELECT 1 FROM message_mappings WHERE source_channel_id = ? AND source_msg_id = ? AND target_channel_id = ?",
        (source_channel_id, source_msg_id, target_channel_id),
    )
    return row is not None


async def _append_log(table: str, fields: tuple[str, str], values: tuple[str, str]) -> None:
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)

    async def action(conn: aiosqlite.Connection):
        await conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", values)
        await conn.execute(
            f"DELETE FROM {table} WHERE id NOT IN (SELECT id FROM {table} ORDER BY id DESC LIMIT ?)",
            (LOG_RETENTION_LIMIT,),
        )

    await _run_in_db(action, commit=True)


async def add_log(level: str, message: str):
    await _append_log("system_logs", ("level", "message"), (level, message))


async def add_sys_log(level: str, message: str):
    await add_log(level, message)


async def add_msg_log(action: str, detail: str):
    await _append_log("message_logs", ("action", "detail"), (action, detail))


async def get_sys_logs_after(last_id: int) -> list:
    return await _fetchall(
        "SELECT id, datetime(created_at, 'localtime'), level, message "
        "FROM system_logs WHERE id > ? ORDER BY id DESC LIMIT 50",
        (last_id,),
    )


async def get_recent_sys_logs(limit: int = LOG_RETENTION_LIMIT) -> list:
    return await _fetchall(
        "SELECT id, datetime(created_at, 'localtime'), level, message "
        "FROM system_logs ORDER BY id DESC LIMIT ?",
        (limit,),
    )


async def get_msg_logs_after(last_id: int) -> list:
    return await _fetchall(
        "SELECT id, datetime(created_at, 'localtime'), action, detail "
        "FROM message_logs WHERE id > ? ORDER BY id DESC LIMIT 50",
        (last_id,),
    )


async def get_recent_msg_logs(limit: int = LOG_RETENTION_LIMIT) -> list:
    return await _fetchall(
        "SELECT id, datetime(created_at, 'localtime'), action, detail "
        "FROM message_logs ORDER BY id DESC LIMIT ?",
        (limit,),
    )


async def clear_sys_logs():
    await _execute("DELETE FROM system_logs", commit=True)


async def clear_msg_logs():
    await _execute("DELETE FROM message_logs", commit=True)


async def get_all_settings() -> dict:
    rows = await _fetchall("SELECT setting_key, setting_value FROM global_settings")
    return {key: value for key, value in rows}


async def update_settings(settings: dict):
    rows = [(key, str(value)) for key, value in settings.items()]
    await _executemany(
        "INSERT OR REPLACE INTO global_settings (setting_key, setting_value) VALUES (?, ?)",
        rows,
        commit=True,
    )


async def add_filter_rule(rule_type: str, pattern: str, replacement: str = "", is_case_sensitive: int = 0):
    await _execute(
        "INSERT INTO filter_rules (rule_type, pattern, replacement, is_case_sensitive) VALUES (?, ?, ?, ?)",
        (rule_type, pattern, replacement, is_case_sensitive),
        commit=True,
    )


async def get_all_filter_rules() -> list:
    return await _fetchall("SELECT id, rule_type, pattern, replacement, is_case_sensitive FROM filter_rules")


async def delete_filter_rule(rule_id: int):
    await _execute("DELETE FROM filter_rules WHERE id = ?", (rule_id,), commit=True)


async def apply_message_filters(text_html: str, has_media: bool, file_name: str) -> tuple[bool, str]:
    del has_media
    rules = await get_all_filter_rules()
    should_skip = False
    new_text = text_html or ""
    for _, rule_type, pattern, replacement, is_case_sensitive in rules:
        flags = 0 if is_case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
            if rule_type in ["drop", "skip_media"]:
                if regex.search(new_text) or (file_name and regex.search(file_name)):
                    should_skip = True
                    break
            elif rule_type in ["replace", "replace_text"] and new_text:
                new_text = regex.sub(replacement or "", new_text)
        except re.error:
            continue
    return should_skip, new_text
