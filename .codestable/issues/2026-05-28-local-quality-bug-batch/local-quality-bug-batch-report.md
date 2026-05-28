---
doc_type: issue-report
issue: 2026-05-28-local-quality-bug-batch
status: reported
severity: P1
tags: [config, media-group, public-poller, temp-cleanup, code-quality]
---

# local-quality-bug-batch issue report

## 1. 问题概述

本批次修复审计和后续定位中确认的 4 个本地工具质量问题。它们不是安全优先级问题，但会影响配置语义、同步映射正确性、公共频道轮询的失败恢复，以及 JSON 媒体组临时文件清理。

## 2. 问题清单

### 2.1 字符串布尔配置被误判

配置归一化直接使用 Python truthiness，导致字符串 `"false"`、`"0"` 等被保存为 `True`。这会让用户在 JSON 配置或前端传值中显式关闭开关时，实际行为仍保持开启。

### 2.2 媒体组兼容异常分支写入目标消息 ID 0

API / history / JSON 媒体组在 topics 兼容异常分支中会调用成功记录逻辑并传入 `target_msg_id=0`。后续重复检查会认为源消息已同步，但回复映射和链接改写拿不到真实目标消息 ID。

### 2.3 公共频道轮询 checkpoint 与发送成功边界不清

公共频道轮询在处理媒体组时先计算 `max_seen_id`，再发送到多个目标。失败时不会写 checkpoint，但部分目标已成功发送后下轮会重复处理同一组，当前代码缺少“整组所有目标都成功后才推进 checkpoint”的显式语义和测试保护。

### 2.4 JSON 媒体组准备阶段异常会泄漏临时文件

JSON 媒体组发送只在准备完成后的发送阶段设置 `finally` 清理临时文件。如果准备循环中前面已经生成临时文件、后续准备抛异常，已生成的临时文件不会被删除。

## 3. 影响范围

- 配置加载与保存：布尔字段开关语义。
- 历史/API/JSON 媒体组同步：消息映射表的正确性。
- 公共频道实时轮询：失败后的重复发送边界。
- JSON 导入：临时目录资源清理。

## 4. 验收标准

- 字符串 `false/0/no/off` 归一化为 `False`，`true/1/yes/on` 归一化为 `True`。
- topics 兼容异常不再写入 `target_msg_id=0` 的成功映射。
- 公共频道轮询只在整组所有目标发送成功后推进 checkpoint，并有失败不推进的测试。
- JSON 媒体组准备阶段抛异常时，之前创建的临时文件会被清理。
- 相关单元测试通过。
