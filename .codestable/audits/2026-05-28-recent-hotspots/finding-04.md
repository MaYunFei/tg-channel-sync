---
doc_type: audit-finding
audit: 2026-05-28-recent-hotspots
finding_id: "bug-04"
nature: bug
severity: P1
confidence: high
suggested_action: cs-issue
status: open
---

# Finding 04：API 媒体组兼容分支用目标消息 ID 0 记录成功

## 速答

API 媒体组复制遇到包含 `topics` 的 `TypeError` 时，会把组内所有消息记录为成功，但目标消息 ID 写成 `0`。

## 关键证据

- [sync_worker/clone/process.py:604-608](../../../sync_worker/clone/process.py#L604-L608) — `if "topics" in str(exc)` 分支对每个 item 执行 `record_success(..., item.id, 0, ...)` 并 `break` —— 没有实际目标消息 ID 却进入成功记录。

## 影响

消息映射表可能出现源消息已成功、目标消息为 0 的记录。后续去重可能跳过未真正发送的消息，回复关系和链接改写也无法解析到有效目标消息，日志状态与真实发送状态不一致。

## 修复方向

兼容分支不应记录普通成功；应改为明确失败、降级到可获得目标消息 ID 的发送路径，或记录可区分的跳过状态。

## 建议动作

`cs-issue`，因为这是确定的状态记录 bug，会污染同步映射数据。
