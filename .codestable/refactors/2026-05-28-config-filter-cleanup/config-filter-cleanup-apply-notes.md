---
doc_type: refactor-apply-notes
refactor: 2026-05-28-config-filter-cleanup
---

# config-filter-cleanup apply notes

## 步骤 1: 补配置与过滤刻画测试

- 完成时间: 2026-05-28
- 改动文件: [tests/test_app_config.py](../../../tests/test_app_config.py), [tests/test_database.py](../../../tests/test_database.py)
- 验证结果: `$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_app_config.py"; python -m unittest discover -s tests -p "test_database.py" -k "filter"` 通过；配置测试 Ran 7 tests, OK；过滤测试 Ran 3 tests, OK
- 偏离: 初版刻画测试把空字符串布尔值和文件名正则转义预期写错；已按当前实现修正为 `realtime_fallback_to_user=""` 得到 False，`skip_media` 文件名规则使用 `r"\.zip$"`

## 步骤 2: 提取配置归一化 helper

- 完成时间: 2026-05-28
- 改动文件: [app_config.py](../../../app_config.py)
- 验证结果: `$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_app_config.py"` 通过，Ran 7 tests, OK
- 偏离: 无

## 步骤 3: 拆分消息过滤规则 helper

- 完成时间: 2026-05-28
- 改动文件: [database.py](../../../database.py)
- 验证结果: `$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_database.py"` 通过，Ran 10 tests, OK
- 偏离: 无

## 步骤 4: 运行相关测试集

- 完成时间: 2026-05-28
- 改动文件: [app_config.py](../../../app_config.py), [database.py](../../../database.py), [tests/test_app_config.py](../../../tests/test_app_config.py), [tests/test_database.py](../../../tests/test_database.py)
- 验证结果: `$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_app_config.py"; python -m unittest discover -s tests -p "test_database.py"` 通过；配置测试 Ran 7 tests, OK；数据库测试 Ran 10 tests, OK
- 偏离: 无
