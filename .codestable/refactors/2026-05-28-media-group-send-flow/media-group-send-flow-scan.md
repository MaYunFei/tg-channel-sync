---
doc_type: refactor-scan
refactor: 2026-05-28-media-group-send-flow
status: user-reviewed
scope: sync_worker/clone/process.py, sync_worker/json_import/process.py, tests/test_sync_services.py, tests/test_json_sync.py
summary: 发现 5 条优化点：结构 3 / 可读性 2；风险低 2 / 中 3 / 高 0
---

# media-group-send-flow scan

## 总览

- 扫描范围：`sync_worker/clone/process.py`、`sync_worker/json_import/process.py`，以及对应测试 `tests/test_sync_services.py`、`tests/test_json_sync.py`。
- 发现 5 条优化点：结构 3 / 可读性 2。
- 按风险：低 2 / 中 3 / 高 0。
- 建议先做：#1 #4 #5（边界清晰、已有测试可自证、主要是提取纯构造逻辑）。
- 建议慎做 / 后做：#2 #3（涉及 clone 下载 / fallback 主流程，虽然行为等价但路径更多，应先补或复用刻画测试）。
- 前置检查 7 条全过：✓
  - 本轮目标是行为等价重构，不包含需求变更或 bug 修复。
  - 目标路径已有 `test_sync_services.py` / `test_json_sync.py` 覆盖媒体组发送、topics 兼容、caption、spoiler、临时文件清理等关键行为。
  - 扫描范围 4 文件，未超过 15 文件 / 3000 行的流程阈值。
  - 未命中生成代码、三方代码、纯风格项或需要架构重划的前置拒绝条件。

## 清单

### #1 提取 JSON 媒体组准备阶段

- **位置**：`sync_worker/json_import/process.py:424-463`
- **分类**：结构
- **现状**：`send_json_media_group` 在主函数内同时收集 `file_entries`、`spoiler_flags`、`prepared_temp_paths`、`rewritten_captions`、`total_bytes`，并夹带缺文件降级判断和链接改写日志。
- **问题**：一个连续准备阶段约 40 行，产出 5 组强相关变量，后续发送阶段依赖这些散落变量，增加理解和后续修改成本。
- **建议**：提取 `_prepare_json_media_group(...)`，返回一个小的准备结果对象或字典，主函数只保留“准备 → 选目标 → 发送 → 记录 → 清理”的流程骨架。
- **建议映射的方法**：M-L2-01 Extract Function；M-L2-07 Introduce Parameter Object
- **风险**：低；该阶段已有 caption、spoiler、缺文件、临时文件清理相关测试覆盖，提取时不改变判断顺序即可。
- **验证**：AI 自证：`$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_json_sync.py"`
- **范围**：约 45 行 / 1 文件

### #2 拆分 clone 媒体组下载与准备阶段

- **位置**：`sync_worker/clone/process.py:615-724`
- **分类**：结构
- **现状**：`sync_media_group` 的 clone 分支内联处理并发下载、失败清理、hash perturb、媒体类型统计、文件大小统计、上传目标选择、缩略图下载。
- **问题**：同一段约 110 行包含“取源文件”和“决定如何上传”两类职责；异常清理和后续发送准备交织，读者需要跨多个局部变量追踪文件生命周期。
- **建议**：先提取 `_prepare_clone_media_group_files(...)` 或同等 helper，集中返回 `downloaded_files`、`file_sizes`、`thumbnail_paths`、`spoiler_flags`、`upload_target` 所需输入；主流程保留发送编排。
- **建议映射的方法**：M-L3-07 Single Responsibility Split；M-L2-01 Extract Function
- **风险**：中；涉及下载失败清理、hash perturb 临时文件和上传目标选择，需依赖现有测试并补一条失败清理刻画测试后执行更稳。
- **验证**：AI 自证：`$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_sync_services.py"`
- **范围**：约 80-120 行 / 1 文件，可能补 1 条测试

### #3 拆分 clone 媒体组发送与 fallback 阶段

- **位置**：`sync_worker/clone/process.py:737-831`
- **分类**：结构
- **现状**：clone 媒体组发送段同时构造 bot/user `media_list`、拼装 reply/quote 参数、执行网络重试、处理 bot 体积限制、fallback 到辅助账号、记录 caption rewrite / quote 日志和保存映射。
- **问题**：发送策略分支和结果落库混在一个嵌套循环内，`actual_sender`、`upload_target`、`sent_group`、`bot_size_limit_hit` 等状态跨分支流动，新增或修正 fallback 行为时很容易碰到无关逻辑。
- **建议**：提取 `_send_clone_media_group(...)`，内部只负责“给定文件与发送上下文，返回 sent_group / final_sender 信息”；映射持久化和日志保留在外层。
- **建议映射的方法**：M-L3-07 Single Responsibility Split；M-L2-05 Decompose Conditional
- **风险**：中；fallback 是高价值但分支较多的行为，建议在 #2 之后执行，并确保 bot 失败改用 user 的路径有测试约束。
- **验证**：AI 自证：`$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_sync_services.py"`
- **范围**：约 90-120 行 / 1 文件，可能补 1 条 fallback 刻画测试

### #4 统一媒体组发送参数构造

- **位置**：`sync_worker/clone/process.py:737-763`、`sync_worker/json_import/process.py:364-378`、`sync_worker/json_import/process.py:480-493`
- **分类**：可读性
- **现状**：clone 和 JSON 路径各自重复构造 `group_items`、`normalized_captions`、bot/user media list、`reply_to_message_id` / quote 参数；同类逻辑在至少 3 处出现。
- **问题**：重复逻辑让 caption 空值、animation→video、reply/quote 参数等细节容易在不同发送路径产生漂移；目前新增一个媒体组参数规则需要改多处。
- **建议**：提取轻量 helper，例如 `_build_group_items(...)`、`_build_media_group_send_kwargs(...)`，先只承接纯数据构造，不移动网络调用。
- **建议映射的方法**：M-L2-01 Extract Function；M-L2-05 Decompose Conditional
- **风险**：低；纯构造逻辑可通过现有 caption、spoiler、document/video、reply/quote 测试验证。
- **验证**：AI 自证：`$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_json_sync.py"; python -m unittest discover -s tests -p "test_sync_services.py"`
- **范围**：约 35-60 行 / 2 文件

### #5 压缩 API 媒体组复制分支的重复 kwargs 构造

- **位置**：`sync_worker/clone/process.py:567-614`
- **分类**：可读性
- **现状**：API 模式下 `captions_changed or group_has_spoiler` 与普通复制分支各自创建 `_base_api_copy_kwargs`、添加 reply / quote，并各自包一层 `execute_with_network_retry(lambda: app.copy_media_group(**kwargs), ...)`。
- **问题**：两条分支核心差异只有 `parse_mode`、`captions`、`has_spoilers`，但重复了约 15 行公共调用骨架；后续调整 retry label 或 reply/quote 参数需要同步改两处。
- **建议**：提取 `_build_api_media_group_copy_kwargs(...)` 或在分支前统一构造 kwargs，再按需追加 captions/spoilers，保留现有异常处理不变。
- **建议映射的方法**：M-L2-01 Extract Function；M-L2-05 Decompose Conditional
- **风险**：低；API topics 兼容已有测试防止重新写入假映射，copy kwargs 提取不改变异常分支即可。
- **验证**：AI 自证：`$env:PYTHONPATH = (Get-Location).Path; python -m unittest discover -s tests -p "test_sync_services.py"`
- **范围**：约 20-35 行 / 1 文件

## 用户选择区

请在下面标记要进入 design 的条目：

- [x] #1 提取 JSON 媒体组准备阶段
- [ ] #2 拆分 clone 媒体组下载与准备阶段
- [ ] #3 拆分 clone 媒体组发送与 fallback 阶段
- [x] #4 统一媒体组发送参数构造
- [x] #5 压缩 API 媒体组复制分支的重复 kwargs 构造
