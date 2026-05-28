---
doc_type: refactor-design
refactor: 2026-05-28-config-filter-cleanup
status: approved
scope: app_config.py 配置归一化；database.py 消息过滤；tests/test_app_config.py 与 tests/test_database.py 对应单元测试
summary: 先补配置/过滤刻画测试，再提取配置归一化 helper，最后拆分消息过滤规则 helper
---

# config-filter-cleanup refactor design

## 1. 本次范围

- 从 scan 勾选：#1 补配置与过滤的刻画测试；#2 提取配置归一化 helper；#3 拆分消息过滤规则的编译与应用逻辑
- 明确不做：
  - 不修复字符串 `"false"` / `"0"` 被 `bool()` 视为 True 的行为；这是行为变更，应另走 issue
  - 不增加正则超时、复杂度限制或缓存；这是安全 / 性能行为变更，不属于本次 refactor
  - 不改变过滤规则类型、执行顺序、文件名匹配规则、invalid regex 跳过语义
- 预估总工作量 / 总风险档位：中 / 中。改动跨 2 个业务文件和 2 个测试文件；主要风险是 helper 提取时误改归一化边界行为或过滤规则顺序

## 2. 前置依赖

- 测试覆盖补齐：必须先补 [tests/test_app_config.py](../../../tests/test_app_config.py) 的字符串布尔 truthiness 刻画测试，以及 [tests/test_database.py](../../../tests/test_database.py) 的 `apply_message_filters` drop / replace / invalid regex 刻画测试
- 调用方搜索：不改公开函数签名，`_normalize_config`、`apply_message_filters` 对外行为保持不变，现有调用方无需迁移
- 其他一次性准备：无

## 3. 执行顺序

### 步骤 1：补配置与过滤刻画测试

- 引用方法：M-L1-04 Characterization Test
- 具体操作：
  - 在 [tests/test_app_config.py](../../../tests/test_app_config.py) 增加测试，固定字符串布尔值当前按 Python truthiness 归一化，例如 `"false"` / `"0"` 仍得到 True
  - 在 [tests/test_database.py](../../../tests/test_database.py) 增加 `apply_message_filters` 自身测试，覆盖：drop/skip 命中文本或文件名后返回 skip；replace/replace_text 顺序替换文本；invalid regex 被跳过且不影响后续规则
- 退出信号：新增测试在当前实现下通过
- 验证责任：AI 自证
- 回滚：删除新增测试，恢复 scan 前测试状态

### 步骤 2：提取配置归一化 helper

- 引用方法：M-L2-01 Extract Function
- 具体操作：
  - 在 [app_config.py](../../../app_config.py) 中提取 `_normalize_str`、`_normalize_bool`、`_normalize_float`、`_normalize_token_list`
  - 让 `_normalize_bool` 显式保持当前 truthiness 语义，不解析字符串布尔
  - 用 helper 替换 `_normalize_config` 中重复的字符串、布尔、浮点和 token list 归一化片段
  - 保留 `_normalize_int` 现有职责和字段输出结构
- 退出信号：[tests/test_app_config.py](../../../tests/test_app_config.py) 全文件通过
- 验证责任：AI 自证
- 回滚：还原 [app_config.py](../../../app_config.py) 中 helper 提取和调用替换

### 步骤 3：拆分消息过滤规则 helper

- 引用方法：M-L2-01 Extract Function
- 具体操作：
  - 在 [database.py](../../../database.py) 中提取 `_compile_filter_regex(pattern, is_case_sensitive)`，返回编译后的 regex；invalid regex 仍由调用处跳过
  - 提取 `_filter_rule_should_drop(regex, text, file_name)`，保持文本和文件名任一命中即跳过
  - 提取 `_apply_filter_rule(rule_type, regex, replacement, text, file_name)`，返回是否 skip 与新文本
  - 保持 `apply_message_filters` 公开签名、规则顺序、invalid regex continue、遇 drop break、`has_media` 忽略语义
- 退出信号：[tests/test_database.py](../../../tests/test_database.py) 全文件通过
- 验证责任：AI 自证
- 回滚：还原 [database.py](../../../database.py) 中 helper 提取和 `apply_message_filters` 主流程

### 步骤 4：运行相关测试集

- 引用方法：M-L1-04 Characterization Test
- 具体操作：运行配置与数据库测试文件，确认两个局部模块没有回归
- 退出信号：`test_app_config.py` 与 `test_database.py` 均退出码为 0
- 验证责任：AI 自证
- 回滚：如果只有新增刻画测试失败，回到对应步骤调整；如果无关测试失败，判断是否环境问题，不能归因则暂停汇报

## 4. 风险与看点

- 主要风险：把配置 helper 提取误做成布尔语义修复，导致字符串配置行为变化
- 主要风险：过滤 helper 拆分时改变规则顺序或 invalid regex 跳过语义
- 容易出错点：`replace` 规则会改变后续规则看到的文本，必须保持顺序累计替换
- 不做行为扩张：不新增过滤规则类型，不改变 `has_media` 当前被忽略的行为，不处理 regex ReDoS
