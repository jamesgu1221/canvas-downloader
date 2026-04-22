# Canvas 课件下载器

自动将 Canvas 平台上各课程的文件下载到本地，保持文件夹层级结构，支持增量同步。

## 安装

```bash
pip install -r requirements.txt
```

## 配置

**1. 获取 API Token**

登录 Canvas → 右上角头像 → 设置 → 下拉找到"已批准的集成" → 点击"新建访问令牌"

**2. 创建 `.env` 文件**

在项目根目录（与 `requirements.txt` 同级）新建 `.env` 文件：

```
CANVAS_API_TOKEN=粘贴你的token
CANVAS_URL=https://oc.sjtu.edu.cn
CANVAS_DOWNLOAD_DIR=D:\OneDrive\Desktop\课程材料
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

# 临时覆盖 .env 中的配置（不修改文件）
python -m canvas_dl --token YOUR_TOKEN --url https://oc.sjtu.edu.cn --dir D:\path\to\dir
```

### 图形界面

双击 `canvas_gui.vbs` 启动。界面提供：

- 立即运行与可视化下载进度
- 定时任务管理（可设置多个每日时间点，如同时设 08:00 和 22:00）
- 下载路径修改（直接写入 `.env`）
- 课程启用/禁用勾选（自动保存，外部改动自动重新加载）

## 文件结构

下载后的文件按以下结构保存：

```
课程材料/
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
- 进度记录保存在项目根目录的 `sync_state.json`（不写入下载目录）
- 被老师锁定 / 隐藏 / 无下载权限的文件会显示 `[跳过]` 并计入跳过数，不影响其他文件下载
- Canvas 上名为 CON、NUL 等 Windows 保留设备名的文件/文件夹会自动在名称末尾追加 `_`（如 `NUL.pdf` → `NUL_.pdf`）
- 课程列表同步到 `courses.json`（项目根目录），可在 GUI 勾选启用/禁用，也可直接编辑文件将 `enabled` 改为 `false`；新发现的课程默认启用
