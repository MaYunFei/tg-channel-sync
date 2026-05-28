---
doc_type: audit-finding
audit: 2026-05-28-recent-hotspots
finding_id: "maintainability-07"
nature: maintainability
severity: P2
confidence: medium
suggested_action: cs-issue
status: open
---

# Finding 07：JSON 媒体组准备阶段异常可能遗留临时文件

## 速答

JSON 媒体组发送在准备阶段收集 `prepared_temp_paths`，但清理逻辑位于发送后的 `finally` 中；如果准备循环中途异常，清理块可能不会执行。

## 关键证据

- [sync_worker/json_import/process.py:426-445](../../../sync_worker/json_import/process.py#L426-L445) — `prepared_temp_paths` 在 `_prepare_json_media_path(...)` 创建临时文件后追加，随后继续 `os.path.getsize(...)` 和构造发送条目 —— 后续任一异常都可能中断准备流程。
- [sync_worker/json_import/process.py:543-561](../../../sync_worker/json_import/process.py#L543-L561) — 删除临时文件的 `finally` 包在发送结果处理附近 —— 准备循环异常不会进入该 `try/finally`。

## 影响

hash 扰动或转换产生的临时媒体文件可能残留在磁盘上。单次影响有限，但大媒体组、多次失败或长期运行会累积磁盘占用。

## 修复方向

把准备循环和发送流程放进同一个覆盖 `prepared_temp_paths` 生命周期的 `try/finally`，确保任何异常路径都清理已创建文件。

## 建议动作

`cs-issue`，因为这是异常路径资源泄漏，需要构造准备阶段失败场景验证。
