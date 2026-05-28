---
doc_type: audit-finding
audit: 2026-05-28-recent-hotspots
finding_id: "maintainability-06"
nature: maintainability
severity: P2
confidence: high
suggested_action: cs-refactor
status: open
---

# Finding 06：字符串布尔配置会被 `bool()` 误判为启用

## 速答

配置归一化直接对多个字段调用 `bool(value)`，当配置来自字符串 `"false"`、`"0"`、`"off"` 时会被 Python 判定为 `True`。

## 关键证据

- [app_config.py:98-106](../../../app_config.py#L98-L106) — `proxy["enabled"] = bool(...)`、`server["auto_open_browser"] = bool(...)` —— 字符串 false 会启用代理或自动打开浏览器。
- [app_config.py:110-124](../../../app_config.py#L110-L124) — `force_send`、`bot_rate_limit_enabled`、`realtime_fallback_to_user`、`realtime_hash_perturb`、`portable_mode`、`debug_terminal_logs` 均使用 `bool(...)` —— 多个关键行为受同类问题影响。

## 影响

通过手工编辑 `config.json`、旧版本迁移或非前端 API 客户端提交字符串布尔值时，禁用项可能反而变为启用。影响包括代理开关、强制发送、Bot 频控、实时 fallback、hash perturb 和 debug 终端日志。

## 修复方向

抽出严格布尔归一化函数，明确接受布尔值、数字和常见字符串，并为异常输入回退默认值。

## 建议动作

`cs-refactor`，因为问题跨多个字段，适合先统一配置归一化逻辑再补测试。
