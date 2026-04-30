# 项目结构

## 根目录

| 文件 | 说明 |
|------|------|
| `.env` | 旧版配置文件；首次启动会自动迁移到用户配置目录 |
| `.env.example` | `.env` 模板 |
| `.gitignore` | 忽略本地配置、运行日志、构建产物、测试临时目录与 Python 缓存 |
| `requirements.txt` | 依赖：canvasapi, requests, python-dotenv, tqdm, PySide6, qfluentwidgets, darkdetect, pytest, PyInstaller |
| `courses.json` | 旧版课程配置；首次启动会自动迁移 |
| `canvas_gui.vbs` | 无黑窗启动 GUI（调 `canvas_dl.gui_qt`） |
| `canvas_gui_qt.py` | PyInstaller GUI 打包入口；正常启动 GUI，保留内部 CLI 兼容开关 |
| `canvas_cli.py` | CLI 同步入口；调试或单独打包命令行版本时使用 |
| `canvas_gui_qt.spec` | PyInstaller 打包配置；生成单文件 GUI exe |
| `pytest.ini` | pytest 配置；指定测试目录与本地包导入路径 |
| `AGENTS.md` | Agent 项目约定：运行命令、配置路径、代码边界与验证注意事项 |
| `README.md` | 用户文档 |
| `STRUCTURE.md` | 项目结构说明 |

## `tests/` 测试

| 文件 | 职责 |
|------|------|
| `test_bug_fixes.py` | 回归测试：`request_delay=0` 保留、磁盘空间不足错误、遍历失败时清理进度事件 |
| `test_client.py` | 下载响应校验：拒绝 Canvas 登录页等 HTML 响应写入目标文件 |
| `test_config.py` | CLI 课程 ID 参数解析与默认 Canvas URL |
| `test_migration.py` | 旧版 `.env` / `courses.json` / `sync_state.json` 迁移到用户配置目录 |
| `test_service.py` | `SyncService` 空课程、磁盘空间不足时的状态保存等核心行为 |
| `test_traversal.py` | Windows 保留名、非法字符等路径清洗规则 |

## `canvas_dl/` 包

| 文件 | 职责 |
|------|------|
| `__main__.py` | CLI 入口；解析参数、拉取课程、调度下载 |
| `config.py` | 从统一配置存储和 CLI 覆盖项加载 `AppConfig` |
| `client.py` | Canvas API 封装；文件下载（含重试） |
| `paths.py` | 用户配置目录与运行时文件路径 |
| `stores.py` | settings/secrets/courses/state 的 JSON 存储与旧文件迁移 |
| `events.py` | 核心同步事件模型与 reporter 协议 |
| `service.py` | `SyncService`：可被 CLI 和 GUI 复用的核心同步编排 |
| `traversal.py` | 递归遍历课程文件夹，生成 `(CanvasFile, local_path)` |
| `state.py` | `SyncState`：按大小+mtime 判断文件是否需要重新下载 |
| `courses_config.py` | `courses.json` 读写；与 Canvas 课程列表同步 |
| `progress.py` | CLI 的 tqdm reporter |

## `canvas_dl/gui_qt/` — PySide6 + qfluentwidgets GUI

| 文件 | 职责 |
|------|------|
| `__main__.py` | GUI 入口；pythonw / windowed exe 下的日志重定向到 `canvas_gui_qt.log` |
| `app.py` | `CanvasApp`：FluentWindow 子类，开 Mica + 装配 NavigationInterface；启动时调用 `_patches.apply_popup_shadow_patch()` |
| `theme.py` | 系统主题检测 + darkdetect 跨线程事件桥（支持「跟随系统」） |
| `_patches.py` | qfluentwidgets popup 渲染补丁：在 Win11+Mica 下关掉下拉框 / tooltip 的 DWM 系统底纹与自动圆角，并禁掉 Qt 自挂的 DropShadowEffect，消除 popup 周围的灰色矩形外框 |
| `pages/_content.py` | 业务页基类：滚动 + 淡入；子类调 `add()`。`OpacityEffect` 常驻避免渲染路径切换闪烁 |
| `pages/home.py` | 主页：后台 QThread 调用 `SyncService`，展示事件进度和日志 |
| `pages/schedule.py` | 定时任务：Windows Task Scheduler 增删改 |
| `pages/courses.py` | 课程管理：SwitchButton 列表 + 文件变更监听 |
| `pages/path.py` | 下载路径：PushSettingCard + 目录选择 |
| `pages/settings.py` | 设置：主题切换 / API Token / 关于 |

## `canvas_dl/util/` — gui_qt 使用的辅助模块

| 文件 | 职责 |
|------|------|
| `env.py` | GUI 配置读写兼容层，实际读写用户配置目录 |
| `schedule.py` | PowerShell Task Scheduler 脚本生成与调用；源码运行用 `pythonw.exe -m canvas_dl`，打包后复用 `CanvasDownloader.exe --canvas-dl-cli` |

## 运行时生成文件

| 文件 | 说明 |
|------|------|
| `settings.json` | Canvas URL、下载目录、请求间隔 |
| `secrets.json` | Canvas API Token |
| `courses.json` | 课程启用/禁用配置 |
| `sync_state.json` | 增量同步状态 |
| `sync_state.lock` | 进程级同步锁，防止 GUI / CLI / 定时任务并发写状态 |
| `canvas_dl.log` | 无窗口模式（Task Scheduler）的标准输出 |
| `canvas_gui_qt.log` | GUI 进程的异常日志 |
| `.legacy_migrated` | 旧版项目根目录配置迁移完成标记 |

运行时文件默认位于 `%APPDATA%\CanvasDownloader`；非 Windows 环境回退到 `~/.canvas-downloader`。
测试或手动隔离运行时可设置 `CANVAS_DL_CONFIG_DIR` 覆盖配置目录。
