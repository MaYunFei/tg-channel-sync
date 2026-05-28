---
doc_type: audit-finding
audit: 2026-05-28-recent-hotspots
finding_id: "security-02"
nature: security
severity: P1
confidence: high
suggested_action: cs-issue
status: open
---

# Finding 02：JSON 导入可引用导出目录外的本地文件

## 速答

JSON 导入 API 接受调用方提供的 `json_path`，媒体解析用 `os.path.join(json_dir, msg[...])` 拼接 JSON 内字段，未校验最终路径仍位于导出目录内。

## 关键证据

- [main.py:723-734](../../../main.py#L723-L734) — `/api/start_sync` 接收表单字段 `json_path: str = Form("")` —— 导入入口信任调用方提供的本地路径。
- [sync_worker/json_import/process.py:575-587](../../../sync_worker/json_import/process.py#L575-L587) — `open(json_path, "r")` 后用 `json_dir = os.path.dirname(os.path.abspath(json_path))` —— JSON 文件路径决定媒体根目录。
- [sync_worker/core/media.py:125-147](../../../sync_worker/core/media.py#L125-L147) — `resolve_json_media` 对 `photo`、`video`、`audio`、`file` 等字段直接 `os.path.join(json_dir, value)` —— 未拒绝 `..`、绝对路径或归一化后逃逸路径。

## 影响

恶意或被篡改的 Telegram export JSON 可引用 `../` 等导出目录外路径。如果该文件存在并进入发送路径，服务可能把本地非预期文件上传到目标频道。该风险与无鉴权 API 组合时影响更大。

## 修复方向

对 JSON 文件路径和媒体路径做归一化边界校验，只允许读取导出目录内的普通文件。

## 建议动作

`cs-issue`，因为这是可验证的文件访问边界缺陷，需要复现用例和修复验证。
