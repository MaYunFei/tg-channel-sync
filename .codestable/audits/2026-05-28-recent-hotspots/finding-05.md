---
doc_type: audit-finding
audit: 2026-05-28-recent-hotspots
finding_id: "security-05"
nature: security
severity: P1
confidence: high
suggested_action: cs-issue
status: open
---

# Finding 05：用户正则过滤规则可能阻塞事件循环

## 速答

消息过滤规则把用户配置的正则直接用 Python `re` 编译并在每条消息上执行，缺少超时或复杂度限制，恶意/错误规则可能造成 ReDoS 式阻塞。

## 关键证据

- [database.py:612-629](../../../database.py#L612-L629) — `apply_message_filters` 遍历 `filter_rules` 后 `re.compile(pattern, flags)`，再执行 `regex.search(...)` / `regex.sub(...)` —— 标准 `re` 没有执行超时。
- [database.py:621-626](../../../database.py#L621-L626) — 同一规则会匹配消息文本和文件名，replace 规则还会执行全局替换 —— 长文本和复杂表达式会放大阻塞影响。

## 影响

一个灾难性回溯正则可以阻塞 async 事件循环，使同步处理、Web 请求或其他任务明显卡顿。即使不是恶意输入，用户误配复杂规则也可能让服务表现为“同步卡死”。

## 修复方向

限制规则复杂度、引入带超时的正则执行方案，或把过滤执行隔离到可超时/可取消的线程或进程边界。

## 建议动作

`cs-issue`，因为这是安全与可用性缺陷，需要定义规则限制和回归用例。
