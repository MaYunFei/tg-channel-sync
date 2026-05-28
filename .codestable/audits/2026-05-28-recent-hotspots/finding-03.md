---
doc_type: audit-finding
audit: 2026-05-28-recent-hotspots
finding_id: "bug-03"
nature: bug
severity: P1
confidence: high
suggested_action: cs-issue
status: open
---

# Finding 03：公开频道轮询可能在发送失败后推进检查点

## 速答

公开频道轮询在处理消息组前先更新 `max_seen_id`，而单条同步路径会吞掉多数异常，最终可能把失败消息记录为已轮询，导致后续不再重试。

## 关键证据

- [sync_worker/realtime/public_poller.py:64-84](../../../sync_worker/realtime/public_poller.py#L64-L84) — `max_seen_id` 在调用 `sync_single_message` / `sync_media_group` 前推进，循环结束后写入 `update_public_user_poll_position` —— 检查点不等同于成功发送点。
- [sync_worker/clone/process.py:522-529](../../../sync_worker/clone/process.py#L522-L529) — `sync_single_message` 对非特定异常只 `await log_sync_error(...)`，没有重新抛出 —— 上层轮询无法得知发送失败。

## 影响

公开频道实时同步遇到临时失败、目标发送异常或普通处理异常时，消息可能被标记为已处理但实际未同步，形成静默丢消息。该路径影响用户对实时同步可靠性的核心预期。

## 修复方向

让轮询检查点只在全部目标成功后推进，或让发送失败明确返回/抛出可供轮询层判断的状态。

## 建议动作

`cs-issue`，因为这是同步正确性 bug，需要构造失败发送场景验证不丢消息。
