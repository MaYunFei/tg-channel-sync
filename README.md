# tg-channel-sync (杏铃同步台)

一个面向 Telegram 频道同步和历史迁移的 Web 工具。
支持实时同步、JSON 导入、API 转发、下载重传，适合做频道搬运、备份恢复和多频道复制。

[GitHub 仓库](https://github.com/RRHTY/tg-channel-sync)  | [Release](https://github.com/RRHTY/tg-channel-sync/releases)

频道内容

<img width="720" alt="频道内容预览 1" src="https://github.com/user-attachments/assets/7d25932c-2cce-4dea-9879-fde967e2fc21" />
<img width="720" alt="频道内容预览 2" src="https://github.com/user-attachments/assets/3f8fb204-08d7-44d2-8b65-16e7ae393224" />

Web页面

<img width="720" alt="Web 页面预览 1" src="https://github.com/user-attachments/assets/c50aa34d-7ee6-443f-995b-901d506e79a7" />
<img width="720" alt="Web 页面预览 2" src="https://github.com/user-attachments/assets/08e29f40-db09-4b5e-b2cf-941a4a36a078" />



-----

## 特性

1. **双模式运行**：仅配置 Bot Token 可做实时同步；补充 TG API 后可用完整历史迁移能力
2. **三种历史迁移方式**：
   - **JSON 导入**：导入 Telegram 官方导出的 JSON 备份
   - **API 转发**：直接转发到目标频道，速度快
   - **下载重传**：重新上传媒体，弱化转发痕迹，支持断点续传与大文件场景
3. **多源聚合同步**：实时模式支持多个源频道同时聚合到同一个目标频道
4. **多 Bot 上传池**：支持多个 Bot Token 共同承担上传，按上传量阈值自动轮换和冷却
5. **本地 Bot API 优先**：下载重传时可优先走本地 Bot API，减少失败后的中断和重试成本
6. **消息过滤与内容处理**：支持按消息类型过滤，并基于正则替换或丢弃文本
7. **回复关系保留**：同步时尽量恢复引用和回复链路
8. **Web UI 控制台**：通过浏览器完成配置、任务管理和日志查看
9. **版本检测**：内置版本文件和更新提示，方便确认是否有新版本
10. **便携式目录**：配置、数据库、日志和临时文件都保存在项目目录内，便于迁移和备份

-----

## 部署与运行

 Windows x64系统推荐直接下载Release中已构建完毕的full版本，无需Python环境： [Release](https://github.com/RRHTY/tg-channel-sync/releases)

-----

### 运行环境要求

  - **Python 3.10+**

### 运行步骤

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

### 如何构建Windows 便携版

如果你希望发布或自用 Windows 便携版，当前仓库已经提供了 `PyInstaller` 打包文件和 PowerShell 构建脚本。

1. 进入项目目录并启用虚拟环境：

    ```powershell
    .\venv\Scripts\activate
    ```

2. 安装依赖并补充打包工具：

    ```powershell
    pip install -r requirements.txt
    pip install pyinstaller
    ```

3. 执行打包脚本：

    ```powershell
    .\build-portable.ps1
    ```

4. 打包完成后，产物会输出到 `dist-portable/`，脚本只保留 zip 文件：
    - `tg-channel-sync-vX.Y.Z-windows-x64-portable.zip`：便携 exe 版
    - `tg-channel-sync-vX.Y.Z-windows-x64-full.zip`：完整运行环境版，包含源码、Python 和 `venv` 中的全部依赖
    - 根目录下临时生成的 `build/` 和 `dist/` 会在打包结束后自动清理

5. 用户使用方式：
    - 解压 zip
    - 便携 exe 版双击 `tg-channel-sync.exe`
    - 完整运行环境版双击 `start.bat`
    - 控制台窗口会启动服务，并在服务就绪后自动打开默认浏览器
    - 如果已经有一个实例在运行，再次双击会直接复用现有实例，不会重复启动第二个服务

便携版运行后会在程序目录旁生成或使用这些文件夹与文件：

1. `config.json`：运行配置
2. `data/`：数据库、日志、session 等运行数据
3. `temp/`：下载重传时使用的临时目录

打包脚本默认使用当前 `VERSION` 文件内容生成压缩包名称。

-----
## ToDo

- [ ] 下载重传模式可选修改文件哈希 (MD5/SHA1 扰动)
- [ ] 支持话题模式频道同步
- [x] 打包为 Windows 便携式程序 (PyInstaller)
- [ ] 导出同步统计报告 (PDF/Excel)
- [x] 支持多源频道聚合到单个目标频道
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
