---
doc_type: audit-finding
audit: 2026-05-28-recent-hotspots
finding_id: "performance-08"
nature: performance
severity: P2
confidence: medium
suggested_action: cs-refactor
status: open
---

# Finding 08：链接改写用全局字符串替换导致重复扫描和计数偏差

## 速答

链接改写先对当前文本取匹配快照，再在每个匹配中调用 `updated_html.replace(original, replaced)`，这会替换所有相同字符串而不是当前 match。

## 关键证据

- [services/sync_services.py:184-196](../../../services/sync_services.py#L184-L196) — `for match in list(pattern.finditer(updated_html))` 后执行 `updated_html = updated_html.replace(original, replaced)` —— 单次循环会替换所有相同链接。
- [services/sync_services.py:186-196](../../../services/sync_services.py#L186-L196) — 匹配列表来自替换前的快照，但 `updated_html` 在循环中持续变化 —— 后续迭代基于旧 match，`rewrite_count` 与实际替换数量可能不一致。

## 影响

重复链接场景下，改写次数统计可能偏小；全局替换也让逻辑难以推理，文本越长、重复链接越多，重复扫描成本越高。当前风险偏可维护性/性能，未看到直接数据破坏路径。

## 修复方向

改为按 match 区间重建字符串，或设计异步可控的替换流程，确保一次 match 只处理一次位置。

## 建议动作

`cs-refactor`，因为这是局部实现方式问题，适合行为不变地重构并补重复链接测试。
