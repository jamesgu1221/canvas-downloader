# Canvas 课件下载器

自动将 Canvas 平台上各课程的文件下载到本地，保持文件夹层级结构，支持增量同步。

> **平台要求**：Python 3.10+。CLI 跨平台可用；GUI 的"定时任务"功能走 Windows Task Scheduler + PowerShell，仅 Windows 可用。

## 安装

```bash
pip install -r requirements.txt
```

## 配置

**1. 获取 API Token**

登录 Canvas → 右上角头像 → 设置 → 下拉找到"已批准的集成" → 点击"新建访问令牌"

**2. 配置 Canvas 连接**

推荐在 GUI 的「设置」页填写 Canvas URL 和 API Token。配置会保存在用户配置目录：

- Windows：`%APPDATA%\CanvasDownloader`
- 其它平台：`~/.canvas-downloader`

也可以直接编辑配置文件：

```json
// settings.json
{
  "canvas_url": "https://oc.sjtu.edu.cn",
  "download_dir": "D:\\Courses",
  "request_delay": 0.3
}
```

```json
// secrets.json
{
  "canvas_api_token": "粘贴你的token"
}
```

旧版本项目根目录下的 `.env`、`courses.json`、`sync_state.json` 会在首次启动时自动迁移到新配置目录；旧文件不会被删除。

## 使用

### 命令行

```bash
# 预览待下载文件（不实际下载）
python -m canvas_dl --dry-run

# 下载所有课程文件
python -m canvas_dl

# 只下载指定课程（课程 ID 从 URL 中获取，如 /courses/87629）
python -m canvas_dl --only-course 87629 12345

# 跳过某门课程
python -m canvas_dl --skip-course 87629

# 临时覆盖 Canvas URL 和下载目录（不修改文件）
python -m canvas_dl --url https://oc.sjtu.edu.cn --dir D:\Courses
```

> 不建议通过 `--token` 传入 API Token；命令行参数可能进入 shell 历史或进程列表。
> 请优先在 GUI 设置页保存，或写入 `secrets.json`。

### 图形界面

双击 `canvas_gui.vbs` 启动（PySide6 + qfluentwidgets，Win11 Mica 风格）。
或在终端运行 `python -m canvas_dl.gui_qt`。界面提供：

- 立即运行与可视化下载进度
- 定时任务管理（可设置多个每日时间点，如同时设 08:00 和 22:00）
- 下载路径修改（写入用户配置目录）
- 课程启用/禁用勾选（自动保存，外部改动自动重新加载）
- 主题切换（跟随系统 / 浅色 / 深色）、Canvas API Token 设置

> GUI 需要 Windows 11 22H2+ 才能看到 Mica 效果；Win10 / 旧版自动降级为常规窗口，不影响功能。
>
> 启动时会自动应用一组 qfluentwidgets popup 渲染补丁（`canvas_dl/gui_qt/_patches.py`），消除 Win11 25H2 + Mica 下的下拉框与 tooltip 周围的灰色矩形外框。

## 文件结构

下载后的文件按以下结构保存：

```
D:\Courses\
├── 课程名A/
│   ├── 文件夹1/
│   │   └── 课件.pdf
│   └── 课件2.pptx
└── 课程名B/
    └── ...
```

## 说明

- 重复运行会自动跳过已下载的文件（按文件大小和修改时间判断）
- 中途按 `Ctrl-C` 会保存进度，下次运行从断点继续
- 进度记录保存在用户配置目录的 `sync_state.json`（不写入下载目录）
- 被老师锁定 / 隐藏 / 无下载权限的文件会显示 `[跳过]` 并计入跳过数，不影响其他文件下载
- Canvas 上名为 CON、NUL 等 Windows 保留设备名的文件/文件夹会自动在名称末尾追加 `_`（如 `NUL.pdf` → `NUL_.pdf`）
- 课程列表同步到用户配置目录的 `courses.json`，可在 GUI 勾选启用/禁用，也可直接编辑文件将 `enabled` 改为 `false`；新发现的课程默认启用

## 开发验证

```bash
python -m compileall canvas_dl canvas_gui_qt.py canvas_cli.py
pytest
```

## 打包 exe

项目提供了 PyInstaller 配置。首次打包前先安装依赖：

```bash
pip install -r requirements.txt
```

然后运行：

```bash
pyinstaller canvas_gui_qt.spec
```

生成文件：

- `dist\CanvasDownloader.exe`：GUI 程序，无控制台窗口。

发布时只需要给用户 `CanvasDownloader.exe`。GUI 中创建定时任务时，源码运行会注册 `pythonw.exe -m canvas_dl`；打包后会复用同一个 `CanvasDownloader.exe --canvas-dl-cli` 运行后台同步，目标机器不需要额外安装 Python。
