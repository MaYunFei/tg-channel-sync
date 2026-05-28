---
doc_type: refactor-scan
refactor: 2026-05-28-config-filter-cleanup
status: user-reviewed
scope: app_config.py 配置归一化；database.py 消息过滤；tests/test_app_config.py 与 tests/test_database.py 对应单元测试
summary: 用户已选择 #1、#2、#3；先补刻画测试，再拆配置归一化 helper，最后拆过滤规则应用 helper
---

# config-filter-cleanup refactor scan

## 顶部总览

扫描范围锁定在 [app_config.py](../../../app_config.py) 的配置归一化、[database.py](../../../database.py) 的消息过滤，以及 [tests/test_app_config.py](../../../tests/test_app_config.py)、[tests/test_database.py](../../../tests/test_database.py) 的对应测试。前置检查结论：本范围适合走 refactor，但 [database.py](../../../database.py) 的 `apply_message_filters` 缺少自身行为测试，涉及它的结构拆分必须先补刻画测试；[app_config.py](../../../app_config.py) 已有部分测试，但布尔字符串当前 truthy 行为未被显式固定，抽 helper 前也建议补小测试。发现 3 条候选：配置 helper 提取 2 条、过滤逻辑拆分 1 条；均为 P2 / 低到中风险。建议顺序：先做 #1 测试补强，再做 #2 配置归一化 helper，最后做 #3 过滤规则 helper。明确不做：不修复字符串 `"false"` / `"0"` 被 `bool()` 视为 True 的行为，不增加正则超时 / 复杂度限制，不改变过滤规则语义。

## 清单条目

### #1 ✓ 补配置与过滤的刻画测试

- 分类：L1 行为等价迁移 / Characterization Test
- 风险：低
- 建议：✓ 建议先做
- 证据：
  - [tests/test_app_config.py](../../../tests/test_app_config.py) 已覆盖 `debug_terminal_logs` 数值转 bool、日志保留数字归一化、加载配置不重写文件，但没有显式覆盖字符串布尔值沿用当前 truthiness 语义。
  - 当前测试中 `apply_message_filters` 只在 JSON 同步调用方里被 mock，没有覆盖 [database.py](../../../database.py) 中实际过滤规则执行逻辑。
- 问题：后续提取 helper 时，如果没有先固定这些边界行为，容易把重构误做成 bug fix，例如把 `"false"` 改成 False，或改变 invalid regex 跳过规则。
- 建议操作：
  - 在 [tests/test_app_config.py](../../../tests/test_app_config.py) 增加一个测试，固定字符串布尔值当前仍按 Python truthiness 处理。
  - 在 [tests/test_database.py](../../../tests/test_database.py) 增加 `apply_message_filters` 的 drop / replace / invalid regex 刻画测试。
- 验证：`$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_app_config.py"` 与 `$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_database.py" -k "filter"`
- 回滚：删除新增测试。

### #2 ✓ 提取配置归一化 helper，减少 `_normalize_config` 重复转换

- 分类：L2 代码级重构 / Extract Function
- 风险：中
- 建议：✓ 建议做
- 证据：
  - [app_config.py](../../../app_config.py) 的 `_normalize_config` 内重复出现多处 `str(...).strip()`、`bool(...)`、`int(str(...).strip() or default)`、`max(..., float(...))` 形式。
  - 现有 `_normalize_int` 已经说明本文件接受“小 helper + 归一化主流程”的风格。
- 问题：归一化规则分散在主函数中，新增配置项时容易复制不一致，也让 `_normalize_config` 同时承担字段编排与类型转换细节。
- 建议操作：
  - 提取保持现有行为的 `_normalize_str`、`_normalize_bool`、`_normalize_float`、`_normalize_token_list`。
  - `_normalize_bool` 必须保留当前 `bool(value or default)` / `bool(value)` 等可观察语义，不在 refactor 中修字符串布尔问题。
  - 将 `_normalize_config` 中重复转换替换为 helper 调用，保持字段顺序和最终配置结构不变。
- 验证：运行 [tests/test_app_config.py](../../../tests/test_app_config.py) 全文件；如果 #1 被勾选，还需新增刻画测试一起通过。
- 回滚：还原 [app_config.py](../../../app_config.py) 中 helper 提取和调用替换。

### #3 ✓ 拆分消息过滤规则的编译与应用逻辑

- 分类：L2 代码级重构 / Extract Function
- 风险：中
- 建议：✓ 建议做，但依赖 #1
- 证据：
  - [database.py](../../../database.py) 的 `apply_message_filters` 同时负责读取规则、编译正则、处理 invalid regex、判断 drop/skip_media、执行 replace/replace_text、维护 `should_skip` 和 `new_text`。
  - `has_media` 当前被 `del has_media` 明确忽略，说明不能借重构改变规则输入语义。
- 问题：函数职责集中，后续扩展规则类型或定位过滤问题时需要同时理解数据库、正则和文本替换流程。
- 建议操作：
  - 提取 `_compile_filter_regex(pattern, is_case_sensitive)`，继续让 invalid regex 被跳过。
  - 提取 `_filter_rule_should_drop(regex, text, file_name)`。
  - 提取 `_apply_filter_rule(rule_type, regex, replacement, text, file_name)` 或等价小 helper，让主函数只保留“取规则 + 顺序应用 + 遇 drop break”。
  - 不增加 regex timeout，不缓存 regex，不改变规则顺序或匹配目标。
- 验证：先运行 #1 中新增的过滤刻画测试，再运行 [tests/test_database.py](../../../tests/test_database.py) 全文件。
- 回滚：还原 [database.py](../../../database.py) 中 helper 提取和 `apply_message_filters` 主流程。
