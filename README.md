# tg-channel-sync (杏铃同步台)

一款基于 Web UI 管理的 Telegram 频道同步、复制、数据迁移工具。采用 FastAPI + Vue3 前后端分离架构，支持频道实时监听、多模式历史数据爬取、断点续传以及基于正则表达式的高级内容过滤。

[GitHub 仓库](https://github.com/RRHTY/tg-channel-sync)

频道内容

<img width="720" alt="频道内容预览 1" src="https://github.com/user-attachments/assets/7d25932c-2cce-4dea-9879-fde967e2fc21" />
<img width="720" alt="频道内容预览 2" src="https://github.com/user-attachments/assets/3f8fb204-08d7-44d2-8b65-16e7ae393224" />

Web页面

<img width="720" alt="Web 页面预览 1" src="https://github.com/user-attachments/assets/c50aa34d-7ee6-443f-995b-901d506e79a7" />
<img width="720" alt="Web 页面预览 2" src="https://github.com/user-attachments/assets/08e29f40-db09-4b5e-b2cf-941a4a36a078" />



-----

## 特性

1. **双模式运行**：仅 Bot Token 即可运行实时同步功能，配置 TG API 后解锁全部历史迁移能力
2. **三种历史同步模式**：
   - **JSON 导入**：读取 Telegram 官方导出的 JSON 本地数据备份并上传
   - **API 转发**：通过 API 无引用直接转发到目标频道，速度极快
   - **下载重传**：下载媒体后重新上传，去除转发特征，支持2GB大文件 (需 API)
3. **实时频道映射**：配置源频道与目标频道的对应关系，新消息自动同步
4. **消息过滤系统**：支持按类型过滤 (文本/图片/视频/文档等)，及基于正则的消息内容替换/丢弃
5. **引用与回复保留**：自动映射并恢复消息间的回复关系，支持同步引用文本
6. **WebUI 控制台**：Vue.js + Tailwind CSS 现代化界面，支持 SSE 实时日志监控
7. **相册智能处理**：自动识别并组合媒体组，保持相册完整性转发
8. **断点续传**：基于 SQLite 记录同步进度，支持随时启停且不丢失位置
9. **FloodWait 自动处理**：触发 Telegram 频率限制时自动计算并休眠，保证任务不中断
10. **便携式设计**：所有配置、数据库与临时文件均在项目目录下，支持快速部署与迁移

-----

## 部署与运行

### 环境要求

  - **Python 3.9+**

### 安装步骤

1.  克隆代码仓库并进入目录：

    ```bash
    git clone https://github.com/RRHTY/tg-channel-sync.git
    cd tg-channel-sync
    ```

2.  安装依赖包：

    ```bash
    pip install -r requirements.txt
    ```

3.  **启动服务**：

    ```bash
    python main.py
    ```

4.  **初始化配置**：

    服务启动后，通过浏览器访问 `http://localhost:8011` 进入初始化向导。
    - **Bot Token**: 必须提供，用于实时同步与基础操作。
    - **API ID / Hash**: 推荐提供，用于解锁历史迁移与大文件传输能力。
    - *(注：若配置了 API ID，首次启动需在 WebUI 控制台面板中完成辅助账号的登录验证)*

-----
## ToDo

- [ ] 下载重传模式可选修改文件哈希 (MD5/SHA1 扰动)
- [ ] 支持话题 (Topics/Threads) 模式频道同步
- [ ] 打包为 Windows 便携式程序 (PyInstaller/Nuitka)
- [ ] 导出同步统计报告 (PDF/Excel)
- [ ] 支持多源频道聚合到单个目标频道
- [ ] 还有什么要干的吗？欢迎在 Issues 提建议！

## 开源协议

本项目采用 [MIT License](LICENSE) 开源。

## 常见问题 (FAQ)

100% AI代码(?)，我是0基础小白，目前用的 Gemini3pro/GLM5/GPT-5.4+Trae 作为IDE，写的这个小工具，有啥简单的功能/代码可读性/性能优化，发issues就可，我可以让AI快快干活！

**Q: 为什么点击“停止任务”后，UI 会出现进度条等待？**
A: 为确保 SQLite 数据不出现脏写及底层网络流的安全释放，程序触发中断时会等待正在执行的网络请求切断并持久化当前断点。等待时间通常在 1\~2 秒。


**Q: 下载重传模式 对服务器有什么要求？**
A: 下载重传模式需将文件先下载至本地 `temp` 目录再上传，因此需要一定的带宽与磁盘空间。单个文件上传成功即删除，但在处理含几十 GB 视频的巨型媒体组时，仍需保证本地有等同于该媒体组大小的临时空间。

## 旧版迁移到新版

`v0.3.0` 起项目完成了一次较大的结构调整，配置与运行数据默认改为便携式目录布局。

从旧版迁移到新版时，建议按下面处理：

1. 旧版 `.env` 不再作为主配置入口，请在新版启动后通过初始化向导或设置页重新写入配置。
2. 如果旧目录下还有根目录数据库，例如 `data.db`、`sync_bot.db`，可以先备份后删除，避免与新版 `data/` 目录下的新数据库混用。
3. 如果旧版已有辅助账号会话，建议在新版中重新登录一次辅助账号，让程序在 `data/sessions/` 下重新生成新的 session 文件。
4. 如需保留旧环境，请先完整备份旧项目目录，再替换为新版文件。

新版便携目录重点如下：

1. `config.json`：运行配置
2. `data/`：数据库、日志、session 等运行数据
3. `temp/`：下载重传时使用的临时文件目录
