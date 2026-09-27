---
name: douyin-download-cookie
display_name: 抖音视频下载(cookie注入)
description_zh: >-
  通过把用户提供的抖音登录态 cookie 注入干净 chromium 配置(v10/DPAPI)，用无头 chromium 渲染视频页，
  从渲染结果提取 douyinvod 播放直链并下载。适用于 Windows + ms-playwright chromium 环境，绕开被安全软件
  拦截的 CDP/DevTools 调试端口。触发场景：下载抖音视频、抖音视频下载、抖音 cookie 下载、用 cookie 下抖音、
  抓取抖音视频。说明：本方案默认下载【带抖音水印】的播放地址；无水印/更高画质需走 a_bogus 签名的接口，
  本环境无法实现，需借助第三方去水印服务。
description_en: >-
  Download Douyin videos by injecting the user's logged-in cookie into a clean Chromium profile (v10/DPAPI),
  rendering the video page headlessly, and extracting the douyinvod play URL. Works on Windows with
  ms-playwright Chromium, bypassing the CDP/DevTools port blocked by security software. Note: the default
  address carries the Douyin watermark; no-watermark/higher quality requires the a_bogus-signed API.
version: "1.0.0"
author: iithink88
---

# 抖音视频下载（cookie 注入 + 无头渲染方案）

## 为什么需要这个技能
抖音真实播放地址走 `aweme/detail` 接口，强制要求 `X-Bogus` + `a_bogus`（WASM 级签名，本机难复现）+ 登录 cookie，
纯程序化 API 路线在本环境一律 403；而无头浏览器自动化又被 360 等安全软件在 TCP 层 DROP 了
`127.0.0.1` 的 DevTools/CDP 调试端口（Playwright、CDP 9222 全起不来）。

本方案**绕开 CDP**：把 cookie 注入干净 chromium 配置，用 `--dump-dom`（走 stdout）渲染视频页，
再从渲染出的 HTML 里提取 `douyinvod` 播放直链下载。实测可成。

## 前置条件
- Windows（DPAPI 依赖）；已安装 **ms-playwright chromium**（`chromium-*`），或任意可用 chromium。
- Python 3.10+，且装了 `httpx` 与 `cryptography`：
  `pip install httpx cryptography`
- 一份**完整的抖音登录态 cookie 明文**（含 `sessionid`、`sid_tt`、`ttwid`、`odin_tt` 等关键项）。

## 如何获取 cookie（交给用户操作）
在已登录抖音的浏览器（如 360/Chrome）里：
1. 打开抖音任意页面，按 F12 → Application → Cookies → `douyin.com`；
2. 或用浏览器扩展（如 "Cookie-Editor"）一键导出为 `name=value; name2=value2; ...` 文本；
3. 把整段文本贴给 AI 即可。

> 注意：cookie 含登录凭证，AI 用完会**删除明文文件**；建议下载后你在浏览器里退出登录或改密一次作废该 cookie。

## AI 操作流程
1. 把用户贴的 cookie 文本原样写入一个文件（如 `douyin_cookie.txt`）。
2. 用受管 Python 运行本技能的脚本：
   ```
   python download_douyin.py --url "<视频链接或video_id>" --cookie-file douyin_cookie.txt
   ```
   - `--url` 支持：完整链接 `https://www.douyin.com/video/xxxx`、纯 `video_id`、短链 `v.douyin.com/...`
   - `--out` 可指定输出 mp4；默认存 `桌面\抖音下载\抖音_<id>.mp4`
   - `--keep-cookie` 保留明文 cookie；`--cleanup-profile` 下载后删浏览器配置
3. 脚本会自动：定位 chromium → 注入并**回读校验** cookie（防二次加密乱码）→ 无头渲染 → 提取直链 → 下载 → 删明文 cookie。
4. 汇报：视频路径、规格（用 `ffprobe` 看分辨率/时长）、以及"默认带水印"的如实说明。

## 关键修复点（务必保留）
- **写入 cookie 后立即回读校验**：早期版本把注入的 cookie 交给浏览器二次启动时被再加密，导致每条值前面挂 12 字节乱码、
  `sessionid` 损坏 → 抖音判未登录弹验证码。现在用同一把 key 回读，确认 `sessionid` 是干净 32 位 hex 才继续。
- 初始化 profile 用 `data:text/html,<title>init</title>` 同步页面，避免 `about:blank` 在无头下挂起。
- 启动 chromium 加 `--proxy-server=direct://` 并 unset 代理环境变量，规避本机失效代理导致网络挂起。
- **cookie 必须先过滤非 ASCII 再放进请求头**：`httpx` 要求 header 值为 ASCII，cookie 里若有中文、
  或被 `errors="replace"` 解码产生的 `�`，会在下载时抛 `UnicodeEncodeError` 直接崩。
  脚本已内置 `sanitize_cookie()`（抖音真实 cookie 均为 ASCII，剔除无损失）。
- **每次运行都用新的 temp profile**：chromium 渲染时会轮换 cookie 加密 key，导致同一 profile
  二次回读全是乱码（不可复用，实测 36 条全解出无效 UTF-8）。脚本每次在 tempdir 建新 profile。

## 局限（如实告知用户）
- **水印**：默认拿到的就是带抖音水印的播放地址。无水印/更高画质需 `a_bogus` 接口，本环境做不了。
- **cookie 有效期**：登录态会过期，失效后重新粘贴最新 cookie 即可。
- **安全软件**：若 chromium 启动本身被拦，此路也不通，需换不受限环境。

## 目录结构
```
douyin-download-cookie/
├── SKILL.md              ← 本文件
└── download_douyin.py    ← 主脚本（注入+渲染+提取+下载+清理）
```
