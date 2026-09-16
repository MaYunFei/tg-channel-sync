# AGENT 指南与开发规范 (Branch: `feat/restricted-link-extractor`)

本文档旨在为后续参与本项目维护、功能迭代的 AI Agent 或开发者提供上下文、架构规范以及已踩坑经验总结。

---

## 核心原则

1. **最小侵入性原则（防上游合并冲突）**
   - 本项目是上游开源项目的 Fork 分支。
   - 所有新增功能必须优先采用**插件化、模块化**方式组织（如放在 `services/` 目录下）。
   - 对原有核心业务文件（如 `sync_worker/`、`database.py`、`main.py` 等）应尽量保持**零修改或仅作为挂载入口微调**，避免上游更新拉取代码时产生复杂的合并冲突。

2. **充分复用原项目基础设施（避免重复踩坑）**
   - 原项目作者在频道全量同步、大文件下载、各种复杂媒体类型发送方面已经踩过了许多 Telegram API 的坑并进行了防御性处理。
   - **在后续编写新功能或修补逻辑时，必须优先查阅并复用原项目的成熟实现**，禁止随意造轮子。
   - 重点复用模块：
     - `sync_worker.core.media.extract_upload_metadata`: 提取视频、音频元数据（`width`, `height`, `duration`, `supports_streaming`）。
     - `sync_worker.clone.helpers._build_temp_download_path`: 下载路径与文件后缀规范。
     - `sync_worker.senders.*`: 媒体组与单文件的上传分发模式。

---

## 本分支（受限链接提取转发）关键设计与实现

### 1. 模块结构
- `services/link_extractor.py`:
  - 核心处理引擎：链接解析（私有/公开消息）、媒体组/单条消息提取、文件临时下载、元数据继承及媒体分发。
- `services/link_extractor_router.py`:
  - 机器人交互层：监听 `/me`、`/saved`、`/to`、`/target`、`/auth` 命令与直接发送的 Telegram 链接。
  - 用户专属 FIFO 异步排队队列（`UserTaskQueue`）：保证同一用户多条消息按顺序串行执行，避免乱序和 API 频控。
- `tests/test_link_extractor.py`:
  - 覆盖测试用例（链接解析、命令路由、授权控制等）。

### 2. 踩坑经验库（务必牢记）

#### ① 频道 ID 的 `-100` 前缀陷阱
- **现象**：当向纯数字频道 ID（例如 `2264185942`）发送消息时，Bot API 会报 `[400 Bad Request: chat not found]`，Pyrogram 会报 `[400 PEER_ID_INVALID]`。
- **原因**：Telegram 底层区分 User ID（正数）与 Channel/Supergroup ID（`-100` 开头的 13 位负数）。如果传入正数，Telegram 会将其误判为用户，导致找不到对应实体。
- **规范**：解析目标 ID 时，对于大于 0 且长度 $\ge 9$ 的正整数，必须自动补齐 `-100` 前缀（`val = int(f"-100{val}")`）。

#### ② 媒体下载的文件名与后缀名缺失
- **现象**：上传相册图片时 Telegram 报错 `[400 PHOTO_EXT_INVALID]`。
- **原因**：Telegram 的 `Photo` 消息对象在下载时不自带文件名，Pyrogram 默认下载的文件无后缀名；而再次通过 Bot API / Pyrogram 打包 `InputMediaPhoto` 时，Telegram 服务端会校验文件扩展名。
- **规范**：下载任何媒体前，必须通过 `_build_download_filename` 明确根据 `media_type` 赋予后缀（如 `.jpg`, `.mp4`, `.mp3`, `.webp`）。

#### ③ 视频比例失真（变成 1:1 方块黑边）与时长丢失
- **现象**：原视频在原频道中为 16:9（如 640x360），但重新上传后在手机端播放变成了 320x320 正方形黑边，且时长显示为 0。
- **原因**：Telegram 服务端收到裸视频文件上传时，**不会自动分析视频宽高与时长**，默认降级为 320x320 占位。
- **规范**：**无论通过 Bot API 还是 Pyrogram 发送视频，必须携带源视频的元数据**：
  - 调用 `sync_worker.core.media.extract_upload_metadata(msg, "video")` 获取 `width`, `height`, `duration`, `supports_streaming=True`。
  - 必须同时调用 `_download_media_thumb` 提取原视频的缩略图并作为 `thumbnail` (Bot API) / `thumb` (Pyrogram) 一并上传。

#### ④ 连续发消息的乱序与 Telegram 频控
- **现象**：用户连续向 Bot 发送多条消息时，文件小的后发先至，导致目标频道消息顺序颠倒，或瞬时并发触发 `FLOOD_WAIT`。
- **规范**：在 `services/link_extractor_router.py` 中必须由 `UserTaskQueue`（基于 `asyncio.Lock`）对同一用户的多条消息强制排队，并向用户提供排队提示信息。

---

## 部署与验证流程

远程服务器环境：
- Docker 容器：`tg-channel-sync`
- 本地工作流：
  1. 本地编写并运行测试：`python -m unittest discover tests`
  2. 提交代码并推送：`git push origin feat/restricted-link-extractor`
  3. 远端拉取并重新构建：
     ```bash
     cd /root/tg-channel-sync
     git pull origin feat/restricted-link-extractor
     docker compose build tg-channel-sync
     docker compose up -d tg-channel-sync
     ```
  4. 检查日志状态：`docker logs -f tg-channel-sync`
