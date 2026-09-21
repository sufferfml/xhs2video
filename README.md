# XHS To Video

将你有权使用的图片制作成 9:16 视频，可添加背景音乐、OCR 关键词高亮、画圈和下划线动画。

**0.2.0a1 · 本地单人实验版。** 仓库名称是 `xhs-to-video`，Python 包名为 `xhs-video-mcp`。
提供本地图片 CLI、MCP stdio 服务，以及实验性小红书链接解析和 OpenClaw 工作流模板。
不提供公共网络服务或多人 Telegram 机器人的安全隔离保证。

## 安装

需要 Python 3.10–3.12（CI 验证范围）、FFmpeg。OCR 标注另需 Tesseract 和中文语言包。

```bash
# 在下载或克隆后的项目根目录中
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

# macOS
brew install ffmpeg tesseract tesseract-lang
# Ubuntu / Debian 可使用：
# sudo apt-get install ffmpeg tesseract-ocr tesseract-ocr-chi-sim
```

只有链接解析功能需要 Chromium：

```bash
python -m playwright install chromium
# Linux 缺少浏览器系统库时：python -m playwright install --with-deps chromium
```

开发及可复现验证使用已提交的 `uv.lock`：

```bash
uv sync --locked --extra dev
uv run --locked pytest
```

依赖与许可证清单见 [第三方说明](THIRD_PARTY_NOTICES.md)。安装会从软件源下载依赖；不会打包分发 FFmpeg、Tesseract、Chromium 或第三方媒体素材。

## 先跑通离线示例

```bash
python examples/make_demo.py --output-dir output/demo
xhs-local-video output/demo/image_000.png output/demo/image_001.png --duration 2 --output-dir output
```

示例图片由脚本生成，不抓取帖子，不需要账号或背景音乐。命令返回一行 JSON，其中 `video_path` 是视频位置。
使用自己的图片时，只需替换图片路径；可选 `--bgm /absolute/path/to/licensed-music.mp3` 和 `--style-prompt '把"HELLO"圈出来'`。

## MCP 客户端配置

将以下对象放到客户端的 MCP 配置中。把路径替换为你实际的虚拟环境解释器和数据目录；JSON 中不能依赖 `~` 或 shell 环境变量自动展开。

```json
{
  "mcpServers": {
    "xhs-video": {
      "command": "/absolute/path/to/project/.venv/bin/python",
      "args": ["-m", "xhs_video_mcp.server"],
      "env": {
        "XHS_OUTPUT_DIR": "/absolute/path/to/private-data/output",
        "XHS_BGM_DIR": "/absolute/path/to/private-data/bgm",
        "XHS_TEMP_DIR": "/absolute/path/to/private-data/tmp"
      }
    }
  }
}
```

只支持本地 stdio。可用工具：`create_video_from_xhs`、`list_bgm_files`、`get_video_config`。
先调用 `get_video_config` 验证连接，再使用你有权处理的链接。CLI 诊断写 stderr，stdout 用于 JSON/MCP 协议。

## 实验性链接解析

这是非官方项目，与小红书无隶属或授权关系。代码许可证不授予帖子、图片、音乐或平台访问权限。
仅处理你有权使用的内容；使用前核对适用的[平台协议](https://agree.xiaohongshu.com/h5/terms/ZXXY20220331001/-1)。
登录限制、验证码或平台页面变化可能使解析失败；本项目不提供绕过登录、验证码或访问控制的功能。
不要用它批量搬运第三方内容，也不要把 Cookie、签名分享链接或抓取结果提交进仓库。

```bash
xhs-image-workflow --url 'https://www.xiaohongshu.com/explore/POST_ID'
# 保存 JSON 返回的 download_dir，下两步显式传递同一个目录
xhs-video-plan-workflow \
  --download-dir /absolute/path/to/job \
  --model-plan-json '{"selected_indices":[1,2],"render_plan":{"duration_per_image_list":[3,3],"bgm":"none","style_prompt":""}}'
xhs-video-render-workflow --download-dir /absolute/path/to/job
```

`latest` 已禁用，防止取到其他任务的素材。模型生成的 JSON 只作为数据验证，不作为 shell 命令执行。
CLI 自身不调用模型，也不需要模型 API Key；模型费用由你的 MCP/OpenClaw 客户端配置决定。

完整的一次性流程：

```bash
xhs-video-workflow --url 'https://www.xiaohongshu.com/explore/POST_ID' --bgm none --duration-per-image 3
```

[OpenClaw 模板](openclaw/skills/xhs-to-video/SKILL.md) 仅供本地单人工作流适配。它要求发送前验证可信会话路由，并保存该任务的具体目录；未验证 Telegram 线上发送。

## 配置、隐私和运行限制

默认数据存于用户可写目录：macOS 为 `~/Library/Application Support/xhs-video-mcp`，Linux 为 `${XDG_DATA_HOME:-~/.local/share}/xhs-video-mcp`，Windows 为 `%LOCALAPPDATA%/xhs-video-mcp`。
导入模块不创建目录，执行任务时才创建。升级后如需继续使用项目目录内的旧 BGM/output，请显式设置环境变量。

| 环境变量 | 用途 |
|---|---|
| `XHS_BGM_DIR` / `XHS_OUTPUT_DIR` / `XHS_TEMP_DIR` | 音乐、结果、临时目录 |
| `XHS_FFMPEG_BIN` / `XHS_TESSERACT_BIN` | 外部可执行程序路径 |
| `XHS_OCR_LANG` | 默认 `chi_sim+eng` |
| `XHS_OCR_PSM` | 首选 OCR 分割模式，默认 4 |
| `XHS_MIN_IMAGE_AREA` / `XHS_MIN_IMAGE_SHORT_SIDE` | 回退解析的尺寸过滤，默认 350000 / 700 |
| `XHS_IMAGE_SOURCE_MODE` / `XHS_IMAGE_FORMAT` | `ci` 或 `auto`；`jpeg`、`webp`、`png` 或 `auto` |

每次最多 20 张图片，单图下载最多 20 MiB，总下载最多 200 MiB，单图最多 4000 万像素。
单图时长最多 12 秒，合计最多 240 秒；解析整体限时 120 秒，单个 FFmpeg 进程 180 秒，单次 OCR 30 秒。
动画可能启动多个有限时进程；这些限制不构成公共服务的资源隔离机制。

导出的图片、视频、计划和诊断留在本机，可能含敏感内容及本机路径；分享前自行检查。
网络下载的临时图片在流程结束时清理；强制退出可能留下临时文件。停止运行后，可自行删除所配置的 tmp 目录。
本项目没有遥测；浏览器解析会联系小红书/CDN，客户端自身的数据处理规则由客户端决定。

## 维护与验证

- [变更记录](CHANGELOG.md) · [贡献说明](CONTRIBUTING.md) · [安全报告](SECURITY.md)
- [发布检查](docs/RELEASE.md) · [本版本验证范围](docs/VALIDATION.md)
- 本机离线测试通过不代表实时抓取、Telegram 发送或所有操作系统已验证。
- 卸载：激活虚拟环境后运行 `python -m pip uninstall xhs-video-mcp`；如由 uv 管理则删除项目虚拟环境。数据目录不会自动删除。

## 许可证

Copyright (C) 2026 sufferfml and contributors. 本项目以 [GNU GPL v3.0 only](LICENSE) 发布。
解析器中的 CDN token 提取和图片地址构造参考/改编自 JoeanAmier 的 XHS-Downloader（GPLv3）；
保留其署名和来源，修改范围见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本许可证不覆盖第三方素材或外部程序。
