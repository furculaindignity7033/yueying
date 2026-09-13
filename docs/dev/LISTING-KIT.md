# 目录站提交清单与文案（yueying 0.2.0，2026-09-12）

已自动完成：PyPI 0.2.0、官方 MCP Registry（io.github.vsh5dvsch7-png/yueying，active）、GitHub 仓库描述与 20 个 topics。
以下每项都需要你本人的账号操作；文案可直接复制。

## 1. awesome-mcp-servers（最重要，高 SEO）
仓库 https://github.com/punkpeye/awesome-mcp-servers ，编辑 README.md 的「🎥 Multimedia Process」一节，按字母顺序插入这一行（v 开头，放在 v 之后 w 之前）：

```
- [vsh5dvsch7-png/yueying](https://github.com/vsh5dvsch7-png/yueying) 🐍 🏠 🍎 🪟 🐧 - Let AI watch videos: timestamped transcripts (platform subtitles or local faster-whisper) and scene-change keyframe contact sheets from local files or YouTube/Bilibili URLs. Fully offline, no API keys.
```

网页操作：打开 README.md → 右上角铅笔「Fork this repository and edit the file」→ 找到该节插入 → Propose changes → Create pull request。
PR 标题：`Add yueying – let AI watch videos (local transcript + keyframes MCP server)`（若由 AI 代提，按其 CONTRIBUTING 要求在标题末尾加 🤖🤖🤖）。
PR 正文：
```
Adds yueying: an MCP server (Python, stdio, `uvx yueying mcp`) that turns local video files or YouTube/Bilibili URLs into a timestamped transcript plus scene-change keyframe contact sheets, fully offline (platform subtitles first, local faster-whisper otherwise; ffmpeg bundled; no API keys). Six tools: watch_video, get_transcript, search_transcript, get_frames, get_frame_at, list_videos.
PyPI: https://pypi.org/project/yueying/ (0.2.0) · Official MCP Registry: io.github.vsh5dvsch7-png/yueying · MIT
- [x] Alphabetical order within the category
- [x] Format with legend emojis (🐍 🏠 🍎 🪟 🐧)
```

## 2. Glama（自动收录，需认领）
https://glama.ai/mcp/servers → 搜 yueying → 用 GitHub 登录 → Claim → Sync Server → 看 /score 页，有低分项告诉我改。

## 3. Cline MCP Marketplace
前提：先在 Cline 里按 llms-install.md 装一次并跑通（我还没在本机装 Cline，装好后我可以陪你测）。
然后到 https://github.com/cline/mcp-marketplace/issues/new/choose 选「Server Submission」：
- Repo: https://github.com/vsh5dvsch7-png/yueying
- Logo: docs/logo-400.png（400×400 PNG）
- Description: Let AI watch videos: local files or YouTube/Bilibili URLs -> timestamped transcript + keyframe contact sheets. Offline, no API key.
- 勾选两项 testing 声明（必须真的测过）。

## 4. cursor.directory
https://cursor.directory → Sign in（GitHub 或 Google）→ Submit → 粘贴仓库地址 https://github.com/vsh5dvsch7-png/yueying → 提交。

## 5. mcp.so
https://mcp.so/submit → 登录 → 仓库地址 → 分类 Multimedia → 安装片段：
```json
{ "mcpServers": { "yueying": { "command": "uvx", "args": ["yueying", "mcp"], "env": { "PYTHONUTF8": "1" } } } }
```
备用：在 https://github.com/chatmcp/mcpso/issues/1 留言仓库链接。

## 6. mcpservers.org
https://mcpservers.org/submit → Name: yueying · Category: Productivity · Description 同上 · Repo 同上 · Contact email 填你自己的。

## 7. mcpmarket.com
https://mcpmarket.com/submit → 粘贴仓库地址。

## 8. LobeHub（可选）
`npm i -g @lobehub/market-cli` → `lhm login` → `lhm github connect` → `lhm plugin publish`（需要我先生成 lhm.plugin.json，你要做再说）。

## 9. PulseMCP
无需操作，从官方 Registry 自动抓取（提交入口暂停中，几周后再看）。

## 10. 发帖（英文优先，一周后再发中文）
- dev.to / Show HN / r/ClaudeAI / r/cursor：主题「Let AI watch videos offline: an MCP server with scene-change contact sheets」，配 docs/demo-grid.jpg，一句基准：6 分钟 B站视频在 RTX 5060 笔记本上约 100 秒出结果。预先回应三个常见质疑：图片 token 成本（九宫格每张约 1–2K token）、隐私边界（画面只发给你自己用的模型）、关键帧漏动作（get_frame_at 可看任意一秒）。
- 中文：小众软件 t/91454 主楼补「v0.2.0 MCP 版」一节；V2EX 分享创造；即刻。

## 0.2.1 再做
- .mcpb 一键安装包（Claude Desktop 目录需要 Google 表单 + Mac 测试）、Smithery 发布。
