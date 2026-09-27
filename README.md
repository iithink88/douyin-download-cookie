# 抖音视频下载技能（cookie 注入 + 无头渲染）

把你的抖音登录态 cookie 注入一个干净的 Chromium 配置，用无头浏览器渲染视频页，
从渲染结果里提取抖音播放直链并下载。**默认下载的是带抖音水印的版本。**

> 适用环境：Windows（依赖系统 DPAPI 加密）+ 已安装 Chromium（ms-playwright 或 Chrome 均可）。

---

## 一、前置条件

1. **Windows 系统**（脚本用到 Windows DPAPI 来加密 cookie）。
2. **Python 3.10+**，并安装两个依赖：
   ```bash
   pip install httpx cryptography
   ```
3. **一个 Chromium 内核浏览器**，满足任一即可：
   - 装了 `ms-playwright` 的 chromium：`pip install playwright && playwright install chromium`
   - 或本机已装 Google Chrome（脚本会自动在 `C:/Program Files/...` 里找）
   - 或手动指定：设置环境变量 `DOUYIN_CHROME_BIN` 指向 `chrome.exe` 路径

---

## 二、三步安装到 WorkBuddy

1. **放文件夹**：把本文件夹 `douyin-download-cookie/` 整个复制到你的 WorkBuddy 技能目录：
   ```
   C:/Users/你的用户名/.workbuddy/skills/douyin-download-cookie/
   ```
   （即和你的其它技能放在一起，WorkBuddy 会自动识别。）

2. **装依赖**（如果还没装）：
   ```bash
   pip install httpx cryptography
   ```

3. **开用**：在 WorkBuddy 里把抖音视频链接 + 你的 cookie 文本给我，我会自动跑：
   ```bash
   python download_douyin.py --url "<视频链接或video_id>" --cookie-file <你的cookie文件.txt>
   ```
   - `--url` 支持：完整链接 `https://www.douyin.com/video/xxxx`、纯 `video_id`、短链 `v.douyin.com/...`
   - 不指定 `--out` 时，默认存到 `桌面\抖音下载\抖音_<id>.mp4`
   - 下载完成后脚本会**自动删除明文 cookie 文件**（安全）

---

## 三、怎么拿到 cookie（你自己操作）

在**已登录抖音**的浏览器（Chrome / 360 等）里：
1. 打开抖音任意页面 → 按 `F12` → `Application` → `Cookies` → `douyin.com`；
2. 或用浏览器扩展（如 **Cookie-Editor**）一键导出为 `name=value; name2=value2; ...` 文本；
3. 把整段文本保存成 `.txt` 文件，或直接与我对话时贴出来即可。

> ⚠️ cookie 含登录凭证。下载完建议在浏览器里退出一次抖音登录或改密，作废这段 cookie。

---

## 四、如实说明的局限

- **水印**：本方案拿到的就是抖音默认播放地址，**带水印**。无水印 / 更高画质需要 `a_bogus` 签名的接口，本环境复现不了，得走第三方去水印服务。
- **cookie 会过期**：登录态失效后，重新导出一份最新 cookie 即可。
- **安全软件**：如果本机把 Chromium 启动都拦了（如某些杀软拦截调试端口），这条路也不通，需要换不受限的环境。

---

## 五、目录结构

```
douyin-download-cookie/
├── SKILL.md              # 技能说明（给 AI 看的）
├── README.md             # 本文件（给你 / 朋友看的）
├── download_douyin.py    # 主脚本：注入 + 渲染 + 提取 + 下载 + 清理
└── cookie.example.txt    # cookie 格式示例（非真实数据）
```
