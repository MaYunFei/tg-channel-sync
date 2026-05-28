---
doc_type: refactor-design
refactor: 2026-05-28-media-group-send-flow
status: draft
scope: sync_worker/clone/process.py 与 sync_worker/json_import/process.py 的低风险媒体组发送流程整理
summary: 先提取 JSON 媒体组准备结果，再统一纯构造逻辑，最后压缩 API 媒体组 copy kwargs 重复代码
---

# media-group-send-flow refactor design

## 1. 本次范围

- 从 scan 勾选：#1 提取 JSON 媒体组准备阶段；#4 统一媒体组发送参数构造；#5 压缩 API 媒体组复制分支的重复 kwargs 构造
- 明确不做：
  - 不做 #2 clone 媒体组下载与准备阶段拆分
  - 不做 #3 clone 媒体组发送与 fallback 阶段拆分
  - 不改变媒体发送策略、fallback 条件、异常处理、日志语义、映射落库条件或临时文件清理边界
  - 不新增功能、不修 bug、不调整公开函数签名
- 预估总工作量 / 总风险档位：中 / 低。改动集中在 2 个实现文件，主要是 helper 提取和重复构造逻辑合并；现有 JSON / clone 媒体组测试可自证

## 2. 前置依赖

- 测试覆盖补齐：本轮低风险项暂不需要先补新测试；现有 `tests/test_json_sync.py` 已覆盖 JSON 媒体组 caption、document/video、spoiler、topics、临时文件清理，`tests/test_sync_services.py` 已覆盖 clone/API topics 与公开轮询媒体组调用边界
- 调用方搜索：不改 `sync_media_group`、`send_json_media_group`、`_send_json_group_via_user` 的公开调用签名；新增 helper 保持模块内私有
- 其他一次性准备：执行过程中每步只做对应 helper 提取，不把 #2/#3 的 clone 主流程拆分混进来

## 3. 执行顺序

### 步骤 1：提取 JSON 媒体组准备阶段

- 引用方法：M-L2-01 Extract Function；M-L2-07 Introduce Parameter Object
- 具体操作：
  - 在 [sync_worker/json_import/process.py](../../../sync_worker/json_import/process.py) 中增加私有准备结果结构，例如 `_JsonMediaGroupPreparation`
  - 提取 `_prepare_json_media_group(...)`，集中处理 `file_entries`、`spoiler_flags`、`prepared_temp_paths`、`rewritten_captions`、`total_bytes`
  - 保持缺媒体文件时返回 `None` 的降级语义，保持链接改写日志和 external source header 选择规则
  - `send_json_media_group` 改成读取准备结果，不改变后续上传目标选择、发送、record_success 和 finally 清理逻辑
- 退出信号：[tests/test_json_sync.py](../../../tests/test_json_sync.py) 全文件通过
- 验证责任：AI 自证
- 回滚：还原 JSON 准备 helper 与结果结构，把准备逻辑恢复到 `send_json_media_group` 内联形式

### 步骤 2：统一媒体组纯构造 helper

- 引用方法：M-L2-01 Extract Function；M-L2-05 Decompose Conditional
- 具体操作：
  - 在 [sync_worker/json_import/process.py](../../../sync_worker/json_import/process.py) 中提取 JSON 媒体组 `group_items` / `normalized_captions` 构造 helper，供 bot 发送与 user fallback 共用
  - 在 [sync_worker/clone/process.py](../../../sync_worker/clone/process.py) 中只提取纯 kwargs / media list 构造 helper；不移动网络调用、不移动 fallback 判断、不移动 record_success
  - 保持 animation 作为 video 发送、空 caption 保持 None / 空串的现有路径语义
  - 保持 reply / quote 参数对 bot 与 user 的差异处理不变
- 退出信号：[tests/test_json_sync.py](../../../tests/test_json_sync.py) 与 [tests/test_sync_services.py](../../../tests/test_sync_services.py) 通过
- 验证责任：AI 自证
- 回滚：删除新增构造 helper，把调用点恢复为原内联构造

### 步骤 3：压缩 API 媒体组复制 kwargs 重复构造

- 引用方法：M-L2-01 Extract Function；M-L2-05 Decompose Conditional
- 具体操作：
  - 在 [sync_worker/clone/process.py](../../../sync_worker/clone/process.py) 中提取 `_build_api_media_group_copy_kwargs(...)` 或等价 helper
  - 统一构造 `chat_id`、`from_chat_id`、`message_id`、reply 和 quote 参数
  - 仅在需要 caption rewrite 或 spoiler 时追加 `parse_mode`、`captions`、`has_spoilers`
  - 保持 `execute_with_network_retry`、topics `TypeError` 兼容处理、restricted forwards 异常处理和 `record_success` 位置不变
- 退出信号：[tests/test_sync_services.py](../../../tests/test_sync_services.py) 全文件通过
- 验证责任：AI 自证
- 回滚：恢复 API 分支内原有两段 kwargs 构造

### 步骤 4：运行相关测试集

- 引用方法：M-L1-04 Characterization Test
- 具体操作：运行 JSON 与同步服务测试，确认两个媒体组路径无回归
- 退出信号：`test_json_sync.py` 与 `test_sync_services.py` 均退出码为 0
- 验证责任：AI 自证
- 回滚：若失败能定位到本轮 helper 提取，回滚对应步骤；若出现无关环境错误，暂停汇报

## 4. 风险与看点

- 主要风险：JSON 准备结果提取时误改缺文件返回 `None`、临时文件 cleanup 或 external source header 插入位置
- 主要风险：统一构造 helper 时误改 bot/user 的 reply / quote 参数差异
- 主要风险：API copy kwargs 合并时误把 `parse_mode`、`captions`、`has_spoilers` 加到普通 copy 路径，导致 Telegram API 行为变化
- 不做行为扩张：不调整 fallback 策略、不改变上传目标选择、不改变日志 tag、不改变 record_success 条件
