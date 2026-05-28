---
doc_type: refactor-scan
refactor: 2026-05-28-sync-link-rewrite
status: user-reviewed
scope: services/sync_services.py 中 rewrite_message_links 及其直接辅助函数；参考 tests/test_sync_services.py 的现有覆盖
summary: 发现 1 条优化点：性能 1；风险：中 1
---

# sync-link-rewrite scan

## 总览

- 扫描范围：[services/sync_services.py:168-216](../../../services/sync_services.py#L168-L216)、[tests/test_sync_services.py:73-96](../../../tests/test_sync_services.py#L73-L96)
- 发现 1 条优化点：结构 0 / 性能 1 / 可读性 0
- 按风险：低 0 / 中 1 / 高 0
- 建议先做：#1（局部函数内改写，已有基础测试，但需要先补重复链接刻画测试）
- 建议慎做 / 后做：无；本次范围过小，不硬凑第二、第三条
- 前置检查 7 条：第 2 条“目标模块有测试覆盖吗”部分通过；已有基础测试覆盖普通链接改写，但重复链接和 rewrite_count 语义未覆盖，进入 design 前应把 #1 的前置依赖设为补刻画测试

## 条目

### #1 按 match 区间重建链接文本 ✓

- **位置**：[services/sync_services.py:184-196](../../../services/sync_services.py#L184-L196)
- **分类**：性能
- **现状**：`replace_pattern` 先取 `list(pattern.finditer(updated_html))`，循环内再对每个 match 执行全局 `updated_html.replace(original, replaced)`。
- **问题**：每个命中都会重新扫描整段文本；相同 `original` 出现多次时，单次循环会替换多个位置，但 `rewrite_count` 只加 1，重复链接场景的替换次数和实际替换位置不一致。
- **建议**：为每个 pattern 收集当前文本里的 match 区间和替换文本，用片段拼接一次性重建字符串，保证一次 match 只处理一个位置；改动前先补重复链接和无映射链接的 characterization test，固化当前期望语义。
- **建议映射的方法**：M-L4-04（N+1 Query Elimination）+ M-L1-04（Characterization Test）
- **风险**：中（会触碰链接改写计数语义；需要测试明确“重复相同链接”到底按位置计数还是按唯一原文计数）
- **验证**：AI 自证（先补并运行 `python -m unittest tests.test_sync_services.SyncServiceTests.test_rewrite_message_links_*`；再运行 `python -m unittest tests.test_sync_services`）
- **范围**：约 35 行 / 1 文件，另需约 20 行测试
