---
doc_type: issue-fix
issue: 2026-05-28-local-quality-bug-batch
status: fixed
severity: P1
tags: [config, media-group, public-poller, temp-cleanup, code-quality]
---

# local-quality-bug-batch fix note

## 修复范围
- 修正字符串布尔配置解析，避免 `"false"` / `"0"` / `"no"` / `"off"` 被 Python 真值规则误判为 `True`。
- 移除媒体组 topics 兼容分支中的 `target_msg_id=0` 假成功映射，避免污染重复检查、回复映射和链接改写。
- 调整公开频道轮询 checkpoint 推进边界，只在整组消息对所有目标发送完成后推进。
- 扩大 JSON 媒体组临时文件清理范围，覆盖准备阶段抛异常的路径。

## 改动文件
- `app_config.py`
- `sync_worker/clone/process.py`
- `sync_worker/json_import/process.py`
- `sync_worker/realtime/public_poller.py`
- `tests/test_app_config.py`
- `tests/test_json_sync.py`
- `tests/test_sync_services.py`

## 验证结果
- `$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_app_config.py"`
  - Ran 7 tests: OK
- `$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_json_sync.py"`
  - Ran 32 tests: OK
- `$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_sync_services.py"`
  - Ran 41 tests: OK

## 结论
本批 P1 本地质量问题已修复并由相关单测覆盖。