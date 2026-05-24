from __future__ import annotations

from ..core import get_msg_meta, is_json_video_file_visual
from .helpers import JSON_MEDIA_GROUP_WINDOW_SECONDS

JSON_MEDIA_GROUP_MAX_ITEMS = 10


def _json_message_timestamp(msg: dict) -> int:
    try:
        return int(msg.get("date_unixtime") or 0)
    except (TypeError, ValueError):
        return 0


def _json_reply_target(msg: dict) -> int:
    try:
        return int(msg.get("reply_to_message_id") or 0)
    except (TypeError, ValueError):
        return 0


def _json_group_family(msg: dict) -> str | None:
    if msg.get("media_type") == "video_file" and msg.get("file"):
        return "visual" if is_json_video_file_visual(msg) else "document"
    msg_type, _ = get_msg_meta(msg, "json")
    if msg_type in {"photo", "video", "animation"}:
        return "visual"
    if msg_type == "audio":
        return "audio"
    if msg_type == "document":
        return "document"
    return None


def _json_can_group_media(msg: dict) -> bool:
    if msg.get("type") != "message":
        return False
    return (_json_group_family(msg) or "") in {"visual", "audio", "document"}


def _json_should_append_to_heuristic_group(group: list[dict], msg: dict, window_seconds: int) -> bool:
    if not group or not _json_can_group_media(msg):
        return False
    if _json_group_family(group[0]) != _json_group_family(msg):
        return False
    prev = group[-1]
    prev_id = int(prev.get("id") or 0)
    curr_id = int(msg.get("id") or 0)
    if curr_id != prev_id + 1:
        return False
    reply_to_message_id = _json_reply_target(msg)
    if reply_to_message_id:
        # Telegram 导出的超长图片串有时会在同一时间窗口内把后续图片记成
        # “回复首图”的普通消息。这里让窗口优先，只要回复目标仍在当前窗口内，
        # 或者整组都回复同一个外部目标，就继续按同一媒体序列归并。
        group_msg_ids = {int(item.get("id") or 0) for item in group}
        group_reply_targets = {_json_reply_target(item) for item in group}
        group_reply_targets.discard(0)
        if reply_to_message_id not in group_msg_ids and group_reply_targets != {reply_to_message_id}:
            return False
    prev_ts = _json_message_timestamp(prev)
    curr_ts = _json_message_timestamp(msg)
    if prev_ts and curr_ts and curr_ts - prev_ts > max(1, int(window_seconds or JSON_MEDIA_GROUP_WINDOW_SECONDS)):
        return False
    return True


def group_json_messages(messages: list[dict], window_seconds: int) -> list[list[dict]]:
    grouped = []
    current_heuristic_group: list[dict] = []

    def append_group_chunk(group: list[dict]):
        if not group:
            return
        for start in range(0, len(group), JSON_MEDIA_GROUP_MAX_ITEMS):
            grouped.append(group[start : start + JSON_MEDIA_GROUP_MAX_ITEMS])

    def flush_heuristic_group():
        nonlocal current_heuristic_group
        if not current_heuristic_group:
            return
        append_group_chunk(current_heuristic_group)
        current_heuristic_group = []

    for msg in messages:
        explicit_group_id = msg.get("media_group_id") or msg.get("grouped_id") or msg.get("media_group")
        if explicit_group_id:
            flush_heuristic_group()
            if grouped and len(grouped[-1]) > 0:
                prev_explicit = grouped[-1][0].get("media_group_id") or grouped[-1][0].get("grouped_id") or grouped[-1][0].get("media_group")
                if prev_explicit == explicit_group_id and len(grouped[-1]) < JSON_MEDIA_GROUP_MAX_ITEMS:
                    grouped[-1].append(msg)
                    continue
            grouped.append([msg])
            continue

        if _json_should_append_to_heuristic_group(current_heuristic_group, msg, window_seconds):
            current_heuristic_group.append(msg)
            continue

        flush_heuristic_group()
        if _json_can_group_media(msg):
            current_heuristic_group = [msg]
        else:
            grouped.append([msg])

    flush_heuristic_group()
    return grouped
