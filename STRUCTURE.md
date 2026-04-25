# 项目结构

## 根目录

| 文件 | 说明 |
|------|------|
| `.env` | API Token、Canvas URL、下载目录 |
| `.env.example` | `.env` 模板 |
| `requirements.txt` | 依赖：canvasapi, requests, python-dotenv, tqdm, PySide6, qfluentwidgets, darkdetect |
| `courses.json` | 课程启用/禁用配置，运行时自动生成和同步 |
| `canvas_gui.vbs` | 无黑窗启动 GUI（调 `canvas_dl.gui_qt`） |
| `README.md` | 用户文档 |

## `canvas_dl/` 包

| 文件 | 职责 |
|------|------|
| `__main__.py` | CLI 入口；解析参数、拉取课程、调度下载 |
| `config.py` | 从 `.env` 加载配置，返回 `AppConfig` |
| `client.py` | Canvas API 封装；文件下载（含重试） |
| `traversal.py` | 递归遍历课程文件夹，生成 `(CanvasFile, local_path)` |
| `state.py` | `SyncState`：按大小+mtime 判断文件是否需要重新下载 |
| `courses_config.py` | `courses.json` 读写；与 Canvas 课程列表同步 |
| `progress.py` | 进度条抽象：CLI 用 tqdm，GUI 用 `@@PROGRESS@@` 管道协议 |

## `canvas_dl/gui_qt/` — PySide6 + qfluentwidgets GUI

| 文件 | 职责 |
|------|------|
| `__main__.py` | GUI 入口；pythonw 下的日志重定向到 `canvas_gui_qt.log` |
| `app.py` | `CanvasApp`：FluentWindow 子类，开 Mica + 装配 NavigationInterface；启动时调用 `_patches.apply_popup_shadow_patch()` |
| `theme.py` | 系统主题检测 + darkdetect 跨线程事件桥（支持「跟随系统」） |
| `_patches.py` | qfluentwidgets popup 渲染补丁：在 Win11+Mica 下关掉下拉框 / tooltip 的 DWM 系统底纹与自动圆角，并禁掉 Qt 自挂的 DropShadowEffect，消除 popup 周围的灰色矩形外框 |
| `pages/_content.py` | 业务页基类：滚动 + 淡入；子类调 `add()`。`OpacityEffect` 常驻避免渲染路径切换闪烁 |
| `pages/home.py` | 主页：立即运行 + 两级进度条 + 日志 |
| `pages/schedule.py` | 定时任务：Windows Task Scheduler 增删改 |
| `pages/courses.py` | 课程管理：SwitchButton 列表 + 文件变更监听 |
| `pages/path.py` | 下载路径：PushSettingCard + 目录选择 |
| `pages/settings.py` | 设置：主题切换 / API Token / 关于 |

## `canvas_dl/util/` — gui_qt 使用的辅助模块

| 文件 | 职责 |
|------|------|
| `env.py` | `.env` 读写（CANVAS_API_TOKEN / CANVAS_DOWNLOAD_DIR / CANVAS_URL） |
| `schedule.py` | PowerShell Task Scheduler 脚本生成与调用 |

## 运行时生成文件

| 文件 | 说明 |
|------|------|
| `courses.json` | 首次运行后生成 |
| `sync_state.json` | 增量同步状态（项目根目录） |
| `canvas_dl.log` | 无窗口模式（Task Scheduler）的标准输出 |
| `canvas_gui_qt.log` | GUI 进程的异常日志 |
