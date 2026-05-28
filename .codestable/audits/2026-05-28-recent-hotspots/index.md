---
doc_type: audit-index
audit: 2026-05-28-recent-hotspots
scope: 近 10 次提交涉及的同步链路、媒体处理、配置、日志和前端热点文件
created: 2026-05-28
status: active
total_findings: 8
---

# recent-hotspots 审计报告

## 范围

本次审计范围按用户确认的“近10次热点”执行，覆盖最近 10 次提交涉及的核心文件：配置与 API 入口、数据库过滤规则、同步服务、JSON 导入、实时公开频道轮询、复制/媒体组发送、媒体解析，以及 `static/app.js` / `static/ui-components.js` 前端热点。扫描维度包括 bug、安全、性能、可维护性和架构偏离。

## 总评

共发现 8 条问题：P1 5 条、P2 3 条；其中安全 3 条、bug 2 条、maintainability 2 条、performance 1 条、arch-drift 0 条。整体看，项目功能迭代较快，核心同步路径已经拆出多个模块，但 Web API 缺少访问边界、JSON 路径处理信任过高、实时同步失败状态传播不一致，是当前最值得优先处理的风险。架构文档目前还是骨架，占位内容不足以支撑严格的架构偏离判断，因此本次未给出 arch-drift 发现。

## 发现清单

| # | 性质 | 严重度 | 置信度 | 标题 | 文件 |
|---|---|---|---|---|---|
| 1 | security | P1 | high | 敏感运行配置可通过无鉴权 API 读取和修改 | [finding-01.md](finding-01.md) |
| 2 | security | P1 | high | JSON 导入可引用导出目录外的本地文件 | [finding-02.md](finding-02.md) |
| 3 | bug | P1 | high | 公开频道轮询可能在发送失败后推进检查点 | [finding-03.md](finding-03.md) |
| 4 | bug | P1 | high | API 媒体组兼容分支用目标消息 ID 0 记录成功 | [finding-04.md](finding-04.md) |
| 5 | security | P1 | high | 用户正则过滤规则可能阻塞事件循环 | [finding-05.md](finding-05.md) |
| 6 | maintainability | P2 | high | 字符串布尔配置会被 `bool()` 误判为启用 | [finding-06.md](finding-06.md) |
| 7 | maintainability | P2 | medium | JSON 媒体组准备阶段异常可能遗留临时文件 | [finding-07.md](finding-07.md) |
| 8 | performance | P2 | medium | 链接改写用全局字符串替换导致重复扫描和计数偏差 | [finding-08.md](finding-08.md) |

## 按维度分布

| 性质 | P0 | P1 | P2 | 合计 |
|---|---|---|---|---|
| bug | 0 | 2 | 0 | 2 |
| security | 0 | 3 | 0 | 3 |
| performance | 0 | 0 | 1 | 1 |
| maintainability | 0 | 0 | 2 | 2 |
| arch-drift | 0 | 0 | 0 | 0 |
| **合计** | **0** | **5** | **3** | **8** |

## 下一步建议

- **P0 立刻修**：无。
- **P1 本迭代修**：Finding 1、2、3、4、5 建议优先开 `cs-issue`，因为它们影响凭证安全、文件边界或同步正确性。
- **P2 有空再看**：Finding 6、7、8 建议走 `cs-refactor` 或小型 `cs-issue`，主要改善配置可靠性、资源清理和热点代码可维护性。
