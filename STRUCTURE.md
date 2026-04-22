# 项目结构

## 根目录

| 文件 | 说明 |
|------|------|
| `.env` | API Token、Canvas URL、下载目录 |
| `.env.example` | `.env` 模板 |
| `requirements.txt` | 依赖：canvasapi, requests, python-dotenv, tqdm |
| `courses.json` | 课程启用/禁用配置，运行时自动生成和同步 |
| `canvas_gui.vbs` | 无黑窗启动 GUI |
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
| `gui.py` | Tkinter GUI：立即运行、定时任务、下载路径、课程勾选、日志 |
| `icon.png` | 窗口图标 |

## 运行时生成文件

| 文件 | 说明 |
|------|------|
| `courses.json` | 首次运行后生成 |
| `sync_state.json` | 增量同步状态（项目根目录） |
| `canvas_dl.log` | 无窗口模式（Task Scheduler）的标准输出 |
| `canvas_gui.log` | GUI 进程的异常日志 |
