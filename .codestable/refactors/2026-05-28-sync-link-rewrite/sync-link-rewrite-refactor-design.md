---
doc_type: refactor-design
refactor: 2026-05-28-sync-link-rewrite
status: approved
scope: services/sync_services.py 中 rewrite_message_links 及其直接辅助函数；tests/test_sync_services.py 中相关单元测试
summary: 为链接改写补重复链接刻画测试，再把全局 replace 改为按 match 区间重建字符串
---

# sync-link-rewrite refactor design

## 1. 本次范围

- 从 scan 勾选：#1 按 match 区间重建链接文本
- 明确不做：不处理 audit 里的安全项；不修复配置布尔解析、JSON 临时文件清理、API 媒体组 ID=0 等行为 bug；不改 `rewrite_message_links` 的公开签名
- 预估总工作量 / 总风险档位：小 / 中。改动集中在 1 个函数和少量测试；风险来自重复链接场景下 `rewrite_count` 语义需要先固定

## 2. 前置依赖

- 测试覆盖补齐：在 [tests/test_sync_services.py](../../../tests/test_sync_services.py) 增加重复链接、未映射链接、用户名链接 / `/c/` 链接组合场景，明确按位置计数
- 调用方搜索：不需要改签名，现有调用方无需迁移
- 其他一次性准备：无

## 3. 执行顺序

### 步骤 1：补链接改写刻画测试

- 引用方法：M-L1-04 Characterization Test
- 具体操作：在 [tests/test_sync_services.py](../../../tests/test_sync_services.py) 的 `SyncServiceTests` 中新增 `test_rewrite_message_links_counts_each_duplicate_position`，用 `AsyncMock` 按消息 ID 返回映射，覆盖同一原始链接重复出现时文本中每个位置都被改写，并且 `rewrite_count` 等于实际改写位置数
- 退出信号：新增测试在当前实现下应暴露计数偏差；如果测试意外通过，说明当前实现已满足该语义，继续执行步骤 2
- 验证责任：AI 自证
- 回滚：删除新增测试，恢复 scan 前状态

### 步骤 2：把全局 replace 改为区间重建

- 引用方法：M-L4-04 N+1 Query Elimination
- 具体操作：改写 [services/sync_services.py](../../../services/sync_services.py) 中 `replace_pattern`：遍历当前文本的 regex match，逐段追加未命中文本和替换文本；每个 match 只查询一次映射并只处理该位置；无映射或替换文本相同则保留原片段；最后用片段列表更新 `updated_html`
- 退出信号：步骤 1 新增测试和既有 `test_rewrite_message_links_*` 全部通过
- 验证责任：AI 自证
- 回滚：还原 [services/sync_services.py](../../../services/sync_services.py) 中 `replace_pattern` 到 scan 前实现

### 步骤 3：运行同步服务测试集

- 引用方法：M-L1-04 Characterization Test
- 具体操作：运行 `python -m unittest tests.test_sync_services`，确认链接改写、频道解析、网络重试等同文件测试没有回归
- 退出信号：测试命令退出码为 0
- 验证责任：AI 自证
- 回滚：如果只有新增行为测试失败，回到步骤 2 调整实现；如果无关测试失败，先判断是否环境问题，不能归因则暂停汇报

## 4. 风险与看点

- 主要风险：重复链接的 `rewrite_count` 从“每轮循环加 1”变为“每个实际位置加 1”，这更符合 scan 中的问题描述，但必须由测试固定
- 容易出错点：同一文本会先跑 `/c/{internal_id}/...` pattern，再跑 username pattern；每次重建必须基于当前 `updated_html`，避免两个 pattern 互相覆盖
- 不做行为扩张：不新增支持其他 Telegram 链接格式，不改变没有映射时保留原文的行为
