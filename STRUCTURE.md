# 项目结构

## 根目录

| 文件 | 说明 |
|------|------|
| `.gitignore` | 忽略本地配置、运行日志、构建产物、测试临时目录与 Python 缓存 |
| `requirements.txt` | 依赖：canvasapi, requests, tqdm, browser-cookie3, PySide6, qfluentwidgets, darkdetect, pytest, PyInstaller |
| `canvas_gui.vbs` | 无黑窗启动 GUI（调 `canvas_dl.gui_qt`） |
| `canvas_gui_qt.py` | PyInstaller GUI 打包入口；正常启动 GUI，保留内部 CLI 兼容开关 |
| `canvas_cli.py` | CLI 同步入口；调试或单独打包命令行版本时使用 |
| `canvas_gui_qt.spec` | PyInstaller 打包配置；生成单文件 GUI exe |
| `pytest.ini` | pytest 配置；指定测试目录与本地包导入路径 |
| `README.md` | 用户文档 |
| `STRUCTURE.md` | 项目结构说明 |

## `docs/` 文档

| 文件 | 说明 |
|------|------|
| `video-download-notes.md` | 课堂视频下载开发笔记：jAccount QR 登录、LTI/token 流程、SJTU 视频接口、GUI/下载注意事项 |

## `tests/` 测试

| 文件 | 职责 |
|------|------|
| `test_bug_fixes.py` | 回归测试：零值配置保留、视频子命令参数透传、cookie 缓存父目录创建、磁盘空间不足错误处理、遍历失败时清理进度事件、开机登录任务计划稳定性 |
| `test_client.py` | 下载响应校验：拒绝 Canvas 登录页等 HTML 响应写入目标文件 |
| `test_config.py` | CLI 课程 ID 参数解析与默认 Canvas URL |
| `test_service.py` | `SyncService` 空课程、磁盘空间不足时的状态保存等核心行为 |
| `test_traversal.py` | Windows 保留名、非法字符等路径清洗规则 |
| `test_videos.py` | 课堂视频：节次解析、Canvas external tool 发现、jAccount QR 辅助、SJTU provider、视频状态与 dry-run |

## `canvas_dl/` 包

| 文件 | 职责 |
|------|------|
| `__main__.py` | CLI 入口；解析参数、拉取课程、调度下载 |
| `config.py` | 从统一配置存储和 CLI 覆盖项加载 `AppConfig` |
| `client.py` | Canvas API 封装；文件下载（含重试） |
| `paths.py` | 用户配置目录与运行时文件路径 |
| `stores.py` | settings/secrets 的 JSON 存储 |
| `events.py` | 核心同步事件模型与 reporter 协议 |
| `service.py` | `SyncService`：可被 CLI 和 GUI 复用的核心同步编排 |
| `traversal.py` | 递归遍历课程文件夹，生成 `(CanvasFile, local_path)` |
| `state.py` | `SyncState`：按大小+mtime 判断文件是否需要重新下载 |
| `courses_config.py` | `courses.json` 读写；与 Canvas 课程列表同步 |
| `progress.py` | CLI 的 tqdm reporter |
| `videos.py` | 课堂视频下载：Canvas external tool 发现、SJTU provider（direct + legacy 双路径）、纯 Python 多线程下载器（HTTP Range / HLS 分片并行 + 跨视频 ThreadPoolExecutor，可在设置里调 K/N）、视频状态 key、视频节次缓存 |
| `browser_cookies.py` | CLI 可选：从本机浏览器 Cookie 存储读取 Canvas/SJTU Cookie |
| `cookie_cache.py` | DPAPI 加密 Cookie 缓存：GUI 扫码登录后加密保存，供 headless CLI 解密使用 |
| `jaccount_qr.py` | GUI 课堂视频登录：jAccount QR 登录、内置 WSS 客户端、external tool/token 解析、课程视频扫描 |

## `canvas_dl/gui_qt/` — PySide6 + qfluentwidgets GUI

| 文件 | 职责 |
|------|------|
| `__main__.py` | GUI 入口；pythonw / windowed exe 下的日志重定向到 `canvas_gui_qt.log` |
| `app.py` | `CanvasApp`：FluentWindow 子类，开 Mica + 装配 NavigationInterface；启动时安装字体和弹层窗口策略 |
| `theme.py` | 系统主题检测 + darkdetect 跨线程事件桥（支持「跟随系统」） |
| `_font_policy.py` | qfluentwidgets 标题字体策略 |
| `_window_policy.py` | qfluentwidgets 弹层窗口策略：在 Win11+Mica 下关闭 popup 的 DWM 背景并移除灰色矩形外框 |
| `pages/_content.py` | 业务页基类：滚动 + 淡入；子类调 `add()`。`OpacityEffect` 常驻避免渲染路径切换闪烁 |
| `pages/_log_panel.py` | 同步日志面板：滚动列表 + 等级配色，供课件下载页和课堂视频页复用 |
| `pages/home.py` | 课件下载页：后台 QThread 调用 `SyncService`，展示事件进度和日志 |
| `pages/schedule.py` | 自动任务：Windows Task Scheduler 增删改 |
| `pages/courses.py` | 课程管理：SwitchButton 列表 + 文件变更监听 |
| `pages/settings.py` | 设置：下载路径、视频下载设置（课堂视频目录 + K/N 并发）、主题切换、Canvas 连接（URL/Token 修改弹窗）、关于 |
| `pages/videos.py` | 课堂视频：扫码登录后急切扫描全部可下载课程的节次和下载 URL；二次进入直接读缓存渲染课程/节次树，按节次正向勾选 → 预览/下载。每门课展开后内置「按节次范围批量勾选」工具栏（`_CourseBatchControls` 通过 `setItemWidget` 挂在 placeholder 子项上）；顶部 [全部展开 / 全部收起]；勾选框使用 `_AnimatedCheckTreeDelegate` 叠加强调色高亮动画 |

## `canvas_dl/util/` — gui_qt 使用的辅助模块

| 文件 | 职责 |
|------|------|
| `env.py` | GUI 配置读写兼容层，实际读写用户配置目录 |
| `schedule.py` | PowerShell Task Scheduler 脚本生成与调用，只管课件同步（`Canvas课件下载`）任务；附带一次性 `cleanup_legacy_video_tasks()` 用于清理老版本残留的 `Canvas视频下载*` 任务 |

## 运行时生成文件

| 文件 | 说明 |
|------|------|
| `settings.json` | Canvas URL、下载目录、视频下载目录（可选，留空则与课件目录相同）、请求间隔、视频并发参数（`video_max_concurrent_videos` / `video_max_workers_per_video`） |
| `secrets.json` | Canvas API Token |
| `courses.json` | 课程启用/禁用配置 |
| `sync_state.json` | 增量同步状态 |
| `sync_state.lock` | 进程级同步锁，防止 GUI / CLI / 定时任务并发写状态 |
| `video_cookies.dat` | DPAPI 加密的 jAccount Cookie，供 headless 视频下载使用 |
| `video_auto_courses.json` | GUI 扫描到的视频课程列表（含 external tool URL 和每门课的 `selected_lectures`） |
| `video_lectures_cache.json` | 视频节次与下载 URL 缓存（按课程 ID 索引，含课程名、缓存时间和每节的全部 asset） |
| `.video_schedule_cleaned` | 哨兵文件；存在则表示老版本残留的 `Canvas视频下载*` 任务已清理过 |
| `canvas_dl.log` | 无窗口模式（Task Scheduler）的标准输出 |
| `canvas_gui_qt.log` | GUI 进程的异常日志 |

运行时文件默认位于 `%APPDATA%\CanvasDownloader`；非 Windows 环境回退到 `~/.canvas-downloader`。
测试或手动隔离运行时可设置 `CANVAS_DL_CONFIG_DIR` 覆盖配置目录。
