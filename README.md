# Canvas 课件下载器

自动将 Canvas 平台上各课程的文件下载到本地，保持文件夹层级结构，支持增量同步。

> **平台要求**：Python 3.10+。CLI 跨平台可用；GUI 的"自动任务"功能走 Windows Task Scheduler + PowerShell，仅 Windows 可用。

## 安装

```bash
pip install -r requirements.txt
```

## 配置

**1. 获取 API Token**

登录 Canvas → 右上角头像 → 设置 → 下拉找到"已批准的集成" → 点击"新建访问令牌"

**2. 配置 Canvas 连接**

推荐在 GUI 的「设置」页 →「Canvas 连接」分组中分别点击「Canvas 实例地址」和
「Canvas API Token」右侧的「修改」按钮，在弹窗中更新连接信息。配置会保存在用户配置目录：

- Windows：`%APPDATA%\CanvasDownloader`
- 其它平台：`~/.canvas-downloader`

也可以直接编辑配置文件：

```json
// settings.json
{
  "canvas_url": "https://oc.sjtu.edu.cn",
  "download_dir": "D:\\Courses",
  "video_download_dir": "",
  "request_delay": 0.3
}
```

> `video_download_dir` 留空则课堂视频与课件共用同一下载目录；填了独立路径则视频单独落到该目录。在 GUI「设置」页的「下载路径」和「视频下载设置」分组中可以分别修改两个目录。

```json
// secrets.json
{
  "canvas_api_token": "粘贴你的token"
}
```

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
> 请优先在 GUI 设置页的「Canvas 连接」分组中修改，或写入 `secrets.json`。

### 课堂视频命令

课堂视频使用单独命令，不影响默认课件同步：

```bash
# 预览指定课程第 1-4、7、10 节的课堂摄像头录像和教师电脑录屏
python -m canvas_dl videos --only-course 87629 --lecture 1-4,7,10 --dry-run

# 下载到单独的视频目录
python -m canvas_dl videos --only-course 87629 --lecture 1-4,7 --dir D:\CourseVideos

# 课堂视频按钮先进入 Canvas external_tools 时，复用本机浏览器登录 Cookie 解析跳转
python -m canvas_dl videos --only-course 87629 --lecture 1-4 --browser-cookies --video-url "https://oc.sjtu.edu.cn/courses/87629/external_tools/8329?display=borderless"
```

#### 无 GUI 复用缓存（headless）

先在 GUI「课堂视频」页扫码登录一次，所有可下载课程的节次与下载 URL 会被全量拉取并缓存（Cookie 由 DPAPI 加密保存）。之后 CLI 无需任何手动参数即可继续下载：

```bash
# 预览所有已扫描课程的视频
python -m canvas_dl videos --cached-cookies --dry-run

# 下载所有已扫描课程的全部视频
python -m canvas_dl videos --cached-cookies

# 从缓存加载认证，但只下载指定课程和节次
python -m canvas_dl videos --cached-cookies --only-course 87629 --lecture 1-4

# 手动传入 external tool 链接 + 缓存 cookie
python -m canvas_dl videos --cached-cookies --video-url "https://oc.sjtu.edu.cn/courses/87629/external_tools/8329?display=borderless"
```

`--cached-cookies` 会自动加载 `video_cookies.dat` 中的加密 jAccount Cookie、`video_auto_courses.json` 中保存的课程列表，以及 `video_lectures_cache.json` 中缓存的视频节次与下载 URL，整个流程不再调用 Canvas / SJTU 接口（命中缓存时）。

视频命令会从 Canvas 课程内容中自动发现 `v.sjtu.edu.cn` 课堂视频入口。若 SJTU 视频应用接口变更或当前账号需要额外浏览器会话，命令会在日志中报告读取视频列表失败；此时需要用浏览器 Network 信息补齐 provider 接口。

### 图形界面

双击 `canvas_gui.vbs` 启动（PySide6 + qfluentwidgets，Win11 Mica 风格）。
或在终端运行 `python -m canvas_dl.gui_qt`。界面提供：

- 「课件下载」页内立即运行与可视化下载进度
- 自动下载任务管理（课件同步：可设置多个每日时间点，也可启用开机登录后下载）
- 设置页内修改下载路径（课件目录和课堂视频目录可分别设置；写入用户配置目录）
- 课程启用/禁用勾选（自动保存，外部改动自动重新加载）
- 课堂视频页：弹出 jAccount 二维码，扫码后一次性拉取所有可下载课程的节次与下载 URL；二次进入直接展示缓存树，按课程 → 节次正向勾选；展开每门课后可在其内部「按节次范围批量勾选」（如 `1-4, 7`）+「反选 / 全清」；顶部 [全部展开 / 全部收起]；勾选框带强调色高亮动画
- 主题切换（跟随系统 / 浅色 / 深色）、Canvas 实例地址与 API Token 修改

> 课堂视频下载内置纯 Python 多线程：单文件用 HTTP Range 切分并行（默认每视频 8 线程），HLS 并发拉取分片后按序拼接；多个视频可同时下载（默认 K=2）。可在「设置」页的「视频下载设置」分组中调整 K（同时下载视频数）和 N（每视频线程数）。进度条会显示当前已下载字节、速度与 ETA。

> GUI 需要 Windows 11 22H2+ 才能看到 Mica 效果；Win10 / 旧版自动降级为常规窗口，不影响功能。
>
> 启动时会安装全局弹层窗口策略（`canvas_dl/gui_qt/_window_policy.py`），消除 Win11 25H2 + Mica 下弹出控件周围的灰色矩形外框。

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

课堂视频开发细节（jAccount 二维码登录、LTI token、SJTU 视频接口和已知坑点）见
`docs/video-download-notes.md`。

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

发布时只需要给用户 `CanvasDownloader.exe`。GUI 中创建课件同步的自动任务时，源码运行会注册 `pythonw.exe -m canvas_dl`；打包后会复用同一个 `CanvasDownloader.exe --canvas-dl-cli` 运行后台同步，目标机器不需要额外安装 Python。课堂视频不参与定时调度（文件体积过大），按需在 GUI 里勾选下载。
