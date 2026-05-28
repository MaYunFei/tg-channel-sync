---
doc_type: refactor-apply-notes
refactor: 2026-05-28-sync-link-rewrite
---

# sync-link-rewrite apply notes

## 步骤 1: 补链接改写刻画测试

- 完成时间: 2026-05-28
- 改动文件: [tests/test_sync_services.py](../../../tests/test_sync_services.py)
- 验证结果: `$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p test_sync_services.py -k test_rewrite_message_links_counts_each_duplicate_position` 通过，Ran 1 test, OK
- 偏离: 新增测试在旧实现下已通过，没有暴露计数偏差；它仍固化了重复链接按位置改写和计数的目标语义

## 步骤 2: 把全局 replace 改为区间重建

- 完成时间: 2026-05-28
- 改动文件: [services/sync_services.py](../../../services/sync_services.py)
- 验证结果: `$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p test_sync_services.py -k "test_rewrite_message_links"` 通过，Ran 3 tests, OK
- 偏离: 无

## 步骤 3: 运行同步服务测试集

- 完成时间: 2026-05-28
- 改动文件: [tests/test_sync_services.py](../../../tests/test_sync_services.py), [services/sync_services.py](../../../services/sync_services.py)
- 验证结果: `$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p test_sync_services.py` 通过，Ran 38 tests, OK
- 偏离: 原 checklist 中的 dotted unittest 路径在当前环境被 `tests` 模块名解析干扰，改用 unittest discovery 指定 `tests` 目录和 `test_sync_services.py` pattern
