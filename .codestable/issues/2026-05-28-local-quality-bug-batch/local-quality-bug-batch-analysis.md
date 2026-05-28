---
doc_type: issue-analysis
issue: 2026-05-28-local-quality-bug-batch
status: analyzed
severity: P1
tags: [config, media-group, public-poller, temp-cleanup, code-quality]
---

# local-quality-bug-batch analysis

## 1. 字符串布尔配置误判

- 根因位置：[app_config.py](../../../app_config.py) 的 `_normalize_bool`。
- 当前逻辑：`bool(value if value is not None else default)`。
- 根因：非空字符串在 Python 中都为真，所以 `"false"`、`"0"` 会变成 `True`。
- 修复方案：对字符串先 `strip().lower()`，显式识别常见真假值；空字符串按默认值处理；非字符串保持原有 truthiness。
- 验证：更新 [tests/test_app_config.py](../../../tests/test_app_config.py) 的字符串布尔测试。

## 2. 媒体组兼容异常写入 ID 0

- 根因位置：[sync_worker/clone/process.py](../../../sync_worker/clone/process.py)、[sync_worker/json_import/process.py](../../../sync_worker/json_import/process.py)。
- 当前逻辑：topics 解析兼容异常分支把发送视为已发生，为避免重复发送调用 `record_success(..., target_msg_id=0)`。
- 根因：`record_success` 会持久化到 `message_mappings`；`0` 不是有效目标消息 ID，却会让重复检查认为已同步，破坏后续回复映射与链接改写。
- 修复方案：兼容异常分支继续停止重试并写日志，但不写入成功映射；JSON 兼容返回值应避免生成 ID 为 0 的假成功消息，或在保存映射前跳过无效 ID。
- 验证：新增/调整测试，确保 topics 兼容异常不调用 `record_success` 写入 0。

## 3. 公共频道轮询 checkpoint 成功边界

- 根因位置：[sync_worker/realtime/public_poller.py](../../../sync_worker/realtime/public_poller.py)。
- 当前逻辑：每个 `message_group` 开始发送目标前先把该组最大消息 ID 合入 `max_seen_id`，全部循环结束后统一写 checkpoint。
- 根因：当前失败不会写 checkpoint，但代码没有表达“只有整组所有目标成功才推进”的边界；未来改动容易把部分成功误记为完成。部分目标成功后整体失败仍会在下轮重复发送，这是为保证未完成目标不丢消息的可接受代价，需要测试固定语义。
- 修复方案：将本组最大 ID 计算为局部 `group_max_id`，仅在该组所有目标发送完成后更新 `completed_max_seen_id`，最终写入完成组的最大 ID。
- 验证：新增公共频道轮询测试，覆盖成功推进和失败不推进。

## 4. JSON 媒体组临时文件泄漏

- 根因位置：[sync_worker/json_import/process.py](../../../sync_worker/json_import/process.py) 的 `send_json_media_group`。
- 当前逻辑：`prepared_temp_paths` 的清理 `finally` 位于准备循环之后，不能覆盖准备阶段异常。
- 根因：准备循环中创建临时文件后，如果后续 `_prepare_json_media_path` 或 `os.path.getsize` 抛异常，函数会直接退出，已记录的临时文件未进入清理 `finally`。
- 修复方案：让准备循环和发送逻辑共享同一个外层 `try/finally`，清理所有已加入 `prepared_temp_paths` 的路径。
- 验证：新增测试模拟第一个媒体创建临时文件、第二个准备抛异常，断言第一个临时文件被删除。
