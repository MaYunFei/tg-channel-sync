---
doc_type: audit-finding
audit: 2026-05-28-recent-hotspots
finding_id: "security-01"
nature: security
severity: P1
confidence: high
suggested_action: cs-issue
status: open
---

# Finding 01：敏感运行配置可通过无鉴权 API 读取和修改

## 速答

[main.py](../../../main.py#L304-L314) 的配置接口直接返回和保存完整运行配置，其中包含 Telegram token、API hash 和代理密码，接口定义处没有可见的鉴权或本地访问边界。

## 关键证据

- [main.py:304-306](../../../main.py#L304-L306) — `@app.get("/api/config")` 直接 `return get_config()` —— 返回完整运行配置。
- [main.py:309-314](../../../main.py#L309-L314) — `payload = await request.json()` 后直接 `save_config(payload)` 并返回 `config` —— 任意可访问 API 的调用方可修改配置。
- [app_config.py:10-24](../../../app_config.py#L10-L24) — 默认配置结构包含 `bot_token`、`extra_bot_tokens`、`api_hash`、`proxy.password` —— 这些字段属于敏感凭证。

## 影响

如果服务被绑定到非本机地址、被反向代理暴露，或本机存在不可信页面/脚本可访问该端口，攻击者可读取 Telegram 凭证或替换运行配置。默认 host 是 `127.0.0.1`，但 host 可配置，因此这是高影响的暴露风险。

## 修复方向

为配置读写 API 增加访问边界，并避免在响应中回传明文敏感字段。

## 建议动作

`cs-issue`，因为这是明确的安全缺陷，需要定义期望访问模型并验证修复闭环。
