# 司凤音造局

把歌词丢进来，剩下的交给 WOO。

这是一个运行在本机的中文 AI 音乐工作台。它不下载 ACE-Step、RVC 或 UVR，也不需要显卡；真正的生成由 Suno API 在云端完成，本地负责歌词精修、价格确认、任务轮询、试听和文件保存。

第一次使用请先看：[使用教程.md](使用教程.md)。

## 快速开始

1. 双击 start.bat，等待浏览器打开。
2. 第一次使用，点击右上角“API 设置”，填入 Suno API Key。Key 只写入本机配置，不会进入网页代码或 localStorage。
3. 在“我有歌词”中粘贴歌词，或点“选择 .txt”读取本地文本。文件只在浏览器本机读取，不会上传。
4. 点“一键司凤化”。程序会补段落、Scat 口技、邦吧邦吧邦吧和句间 WOO，并给 Woo / Yeah / Uh / Ho / Hey 等语气词加上 [scream]。
5. 点“生成司凤音乐”。页面先请求实时价格，再弹出确认框，明确显示会发送到 suno-api.io 的歌词字数、提示词字数和预计费用。
6. 确认后生成约 2 个版本。完成后可直接试听，并下载 MP3 或 WAV 到本机 outputs/ 目录。

取消确认框不会提交任务，也不会扣费。

## 只有一个想法

切到“只有一个想法”：

- 免费起草：在本机生成一版司凤风格草稿，不上传，不调用云端。
- AI 写词 · Suno：把想法和风格提示词发送到 suno-api.io，由云端写词，再自动做一次本地司凤化。通常免费，超出免费配额时平台可能收取约 ¥0.10；提交前必须勾选确认。

## 司凤化规则

- 自动拆分或补齐 Intro / Verse / Pre-Chorus / Drop / Scat Break / Outro。
- 句间补 Woo / Yeah / Uh / Ho / Hey 等 ad-lib。
- 所有语气词前自动加 [scream]，例如 `[scream] (WOO!!!)`、`[scream] WOO!`。
- 呼哈、邦吧邦吧邦吧、乌鲁鲁、通透、收等非语气词不会误加标记。
- 重复点击“一键司凤化”不会产生 `[scream] [scream]`。

## 进阶设置

展开“进阶设置”可以修改：

- 歌曲标题。
- 风格提示词。默认配方包含 128 BPM four-on-the-floor、synth-pluck、sidechain bass、烟嗓、WOO 尖叫、jazz scat、melisma、vocal fry 等。
- 模型：Suno v6 / v6 Wild / v6 Mini，默认 v6 Wild。
- Max 模式：更长、更完整，价格 ×2。

“恢复司凤默认”可以随时把风格提示词还原。

## 已实现

- 单页简约工作台，只监听 127.0.0.1。
- 粘贴歌词、本地读取 .txt / .md、UTF-8 与 GBK 自动识别。
- 本地歌词司凤化：段落重组、Scat Break、语气词 [scream] 标记、beng-ba-beng-ba、邦吧邦吧邦吧、乌鲁鲁。
- 本地想法起草，以及可选的 Suno AI 写词。
- 实时估价、余额显示、付费前二次确认、价格变化保护。
- 异步任务轮询、失败提示、双版本试听。
- MP3 / WAV 导出到 outputs/，云端链接过期后本地副本仍可用。
- 刷新页面不会恢复完整 API Key，也不会把 Key 写入浏览器存储。

## 网络与代理

工作台默认绕过 Windows 系统代理，直连 https://www.suno-api.io。这样可以避开本机代理未运行、代理 SSL 异常导致的连接失败。

需要指定代理时，在启动前设置：

    $env:SIFENG_PROXY='http://127.0.0.1:7897'
    python app.py

确实要使用 Windows 系统代理注册表设置时：

    $env:SIFENG_USE_SYSTEM_PROXY='1'
    python app.py

不设置这两个变量时，程序不会读取系统代理。

如果右上角显示“请求太频繁”，表示平台返回了 429。程序会自动进入约 60 秒冷却，期间不会继续请求平台；等待后再点“检查 API”即可。

## 安全与费用

- API Key 只读取用户目录下的 .suno-api/config.json，页面只显示脱敏值，例如 sk-s…XhqA。
- 当前余额来自账户查询；成功后缓存约 45 秒，避免频繁触发平台限流。
- 普通生成基础价为 0.6 CNY/次，以界面实时估价为准。
- Max 模式价格按平台倍率计算，默认 ×2。
- AI 写词通常免费，超出配额可能约 0.10 CNY/次。
- 任务超时后程序不会自动重试付费请求，应先检查历史任务。
- 媒体下载链接约 7 天有效，请及时导出到本地。
- 所有 AI 写词和付费生成都需要明确确认。

## 目录

- app.py：本地 HTTP 服务、Suno API 客户端、歌词处理、任务与文件管理。
- web/simple.html、web/simple.css、web/simple.js：简约工作台界面。
- outputs/：导出的 MP3 / WAV。
- data/state.json：任务历史和下载记录。
- tests/test_app.py：不产生网络费用的单元测试。
- start.bat / start.ps1：Windows 启动入口。

## 启动

双击 start.bat，或在 PowerShell 中运行：

    cd "D:\vscodepj\3.A2B\thiphon music"
    python app.py

不想自动打开浏览器：

    python app.py --no-browser

指定端口：

    python app.py --port 8877 --no-browser

不需要创建虚拟环境，也没有第三方 Python 依赖。Python 3.10+ 即可。

## 自检

    python -m py_compile app.py
    node --check web/simple.js
    python -m unittest discover -s tests -v

Suno API 和 Agent Kit 的安装状态可用以下命令复查：

    codex mcp get suno-api
    node "$HOME\.suno-api\agent-kit\suno-agent.mjs" doctor
