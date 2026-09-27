#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音视频下载（cookie 注入 + 无头渲染方案）
========================================
适用环境：Windows + 已安装 ms-playwright chromium（或其它可用 chromium）。

原理：
  抖音真实播放地址走 aweme/detail 接口，强制要求 X-Bogus + a_bogus 签名 + 登录 cookie，
  纯程序化复现 a_bogus 在本机不可行；而无头浏览器自动化又被 360 等安全软件在 TCP 层
  拦截了 DevTools/CDP 调试端口。本方案改用：
    1. 把用户提供的登录态 cookie 通过 v10/DPAPI 注入一个干净 chromium 配置目录；
    2. 用无头 chromium 的 --dump-dom 渲染视频页（走 stdout，不需要 CDP 端口）；
    3. 从渲染出的页面中提取 douyinvod 播放直链并立即下载。

重要说明：
  - 本方案拿到的默认播放地址【带抖音水印】。无水印/更高画质需走 a_bogus 签名的接口，
    本环境无法实现，需借助第三方去水印服务。
  - 用完默认会删除明文 cookie 文件；浏览器配置为 DPAPI 加密，仅本机当前账户可读。
  - 关键修复：写入 cookie 后会立即回读校验（防止被浏览器二次加密产生乱码前缀导致
    sessionid 损坏、抖音判为未登录弹验证码）。
"""
import os, sys, json, base64, sqlite3, secrets, argparse, subprocess, glob, shutil, re, ctypes, tempfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ---------------- 定位 chromium ----------------
def find_chrome():
    env = os.environ.get("DOUYIN_CHROME_BIN")
    if env and os.path.isfile(env):
        return env
    patterns = [
        os.path.expandvars(r"C:/Users/*/AppData/Local/ms-playwright/chromium-*/chrome-win64/chrome.exe"),
        r"C:/Users/*/AppData/Local/ms-playwright/chromium-*/chrome-linux/chrome",
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe",
              r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
              "/usr/bin/google-chrome", "/usr/bin/chromium"):
        if os.path.isfile(p):
            return p
    raise SystemExit("找不到 chromium，请设置环境变量 DOUYIN_CHROME_BIN 指向 chrome 可执行文件")


# ---------------- DPAPI ----------------
crypt32 = ctypes.windll.crypt32
kernel32 = ctypes.windll.kernel32
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint), ("pbData", ctypes.POINTER(ctypes.c_char))]
def dpapi_protect(data: bytes) -> bytes:
    inp = _BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = _BLOB()
    if crypt32.CryptProtectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)) == 0:
        raise ctypes.WinError()
    buf = ctypes.string_at(out.pbData, out.cbData)
    kernel32.LocalFree(out.pbData)
    return buf
def dpapi_unprotect(data: bytes):
    inp = _BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = _BLOB()
    if crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)) == 0:
        return None
    buf = ctypes.string_at(out.pbData, out.cbData)
    kernel32.LocalFree(out.pbData)
    return buf


# ---------------- cookie 解析 ----------------
def parse_cookie(raw: str):
    raw = raw.strip()
    if raw.startswith("="):
        raw = raw[1:]
    out = []
    for p in raw.split(";"):
        p = p.strip()
        if "=" not in p:
            continue
        n, _, v = p.partition("=")
        n, v = n.strip(), v.strip()
        if n:
            out.append((n, v))
    return out


def sanitize_cookie(cookies):
    """过滤非 ASCII / 空名 cookie。
    httpx 要求 header 值必须是 ASCII，含中文或替换字符(\\ufffd)会抛 UnicodeEncodeError，
    因此提前剔除（抖音真实 cookie 均为 ASCII，剔除无损失）。
    """
    out = []
    for n, v in cookies:
        if not n:
            continue
        try:
            n.encode("ascii")
            v.encode("ascii")
        except UnicodeEncodeError:
            continue
        out.append((n, v))
    return out


# ---------------- 构建并校验 profile ----------------
def build_profile(cookies, chrome: str, profile_dir: str):
    os.makedirs(os.path.join(profile_dir, "Default", "Network"), exist_ok=True)

    # 1) 初始化 profile（用同步页面避免 about:blank 挂起）
    init = [chrome, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-gpu", "--proxy-server=direct://", f"--user-data-dir={profile_dir}",
            "--dump-dom", "data:text/html,<title>init</title>"]
    try:
        subprocess.run(init, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
    except subprocess.TimeoutExpired:
        pass

    # 2) Local State -> v10（单层 DPAPI，非管理员也能解）
    ls_path = os.path.join(profile_dir, "Local State")
    ls = json.load(open(ls_path, encoding="utf-8"))
    aes_key = secrets.token_bytes(32)
    ls["os_crypt"] = {
        "encrypted_key": base64.b64encode(b"DPAPI" + dpapi_protect(aes_key)).decode(),
        "app_bound_encrypted_key": "",
    }
    json.dump(ls, open(ls_path, "w", encoding="utf-8"))

    # 3) 写 cookie（v10 加密）
    db = os.path.join(profile_dir, "Default", "Network", "Cookies")
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(cookies)")]
    def enc(v: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return b"v10" + nonce + AESGCM(aes_key).encrypt(nonce, v.encode("utf-8"), None)
    now = 0x7FFFFFFFFFFFFFFF
    ins = [c for c in cols if c != "id"]
    ph = ",".join("?" for _ in ins)
    for name, value in cookies:
        row = []
        for c in ins:
            if c == "host_key": row.append(".douyin.com")
            elif c == "name": row.append(name)
            elif c == "value": row.append("")
            elif c == "encrypted_value": row.append(enc(value))
            elif c == "path": row.append("/")
            elif c == "expires_utc": row.append(now)
            elif c == "is_secure": row.append(1)
            elif c == "is_httponly": row.append(0)
            elif c == "has_expires": row.append(1)
            elif c == "is_persistent": row.append(1)
            elif c in ("creation_utc", "last_access_utc", "last_update_utc"): row.append(now)
            else: row.append(0)
        cur.execute(f"INSERT OR REPLACE INTO cookies ({','.join(ins)}) VALUES ({ph})", row)
    conn.commit()
    conn.close()

    # 4) 立即回读校验（关键：防止二次加密乱码导致 sessionid 损坏）
    blob = base64.b64decode(json.load(open(ls_path, encoding="utf-8"))["os_crypt"]["encrypted_key"])
    key = dpapi_unprotect(blob[5:])
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT name, CAST(encrypted_value AS BLOB) FROM cookies")
    bad = 0
    for name, e in cur.fetchall():
        if not e or not e.startswith(b"v10"):
            continue
        try:
            pt = AESGCM(key).decrypt(e[3:15], e[15:], None).decode("utf-8", "replace")
        except Exception:
            bad += 1
            continue
        if name == "sessionid" and not (len(pt) == 32 and all(ch in "0123456789abcdef" for ch in pt)):
            bad += 1
    conn.close()
    if bad:
        raise SystemExit(f"[校验失败] {bad} 条关键 cookie 损坏，请重试（建议重新粘贴最新 cookie）")
    return profile_dir


# ---------------- 解析 video id ----------------
def resolve_video_id(url_or_id: str):
    s = url_or_id.strip()
    if re.fullmatch(r"\d{15,20}", s):
        return s
    m = re.search(r"/video/(\d{15,20})", s)
    if m:
        return m.group(1)
    if "douyin.com" in s:  # 含短链 v.douyin.com/...
        try:
            import httpx
            for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
                os.environ.pop(k, None)
            r = httpx.get(s, follow_redirects=True, timeout=20,
                          headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"})
            m = re.search(r"/video/(\d{15,20})", str(r.url))
            if m:
                return m.group(1)
        except Exception:
            pass
    m = re.search(r"(\d{15,20})", s)
    if m:
        return m.group(1)
    raise SystemExit("无法从输入解析出抖音 video_id")


# ---------------- 无头渲染页面 ----------------
def render_page(chrome: str, profile_dir: str, page_url: str, budget: int = 12000):
    fd, out = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    cmd = [chrome, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
           "--disable-gpu", "--proxy-server=direct://", f"--user-data-dir={profile_dir}",
           f"--virtual-time-budget={budget}", "--dump-dom", page_url]
    try:
        with open(out, "w", encoding="utf-8") as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.DEVNULL, timeout=70)
    except subprocess.TimeoutExpired:
        pass
    html = open(out, encoding="utf-8", errors="replace").read()
    os.remove(out)
    return html


# ---------------- 提取播放地址 ----------------
def extract_play_url(html: str):
    def clean(u: str) -> str:
        return u.replace("\\/", "/").replace("&amp;", "&").replace("\\u002F", "/")

    # 1) douyinvod 直链（页面 playUrl 字段 / video 标签）
    hits = re.findall(r'(https?:\\?/\\?/[^\"\\\s]+?douyinvod[^\"\\\s]+)', html)
    urls = sorted(set(clean(u) for u in hits))
    if urls:
        return urls[0]

    # 2) RENDER_DATA JSON 里递归找 playAddr / downloadAddr / bitRate.playAddr
    m = re.search(r'id="RENDER_DATA"\s+type="application/json">([^<]+)</script>', html)
    if m:
        try:
            import html as hl
            data = json.loads(hl.unescape(m.group(1)))
            def find(o):
                if isinstance(o, dict):
                    for k in ("playAddr", "downloadAddr"):
                        if isinstance(o.get(k), str) and o[k].startswith("http"):
                            return o[k]
                    if isinstance(o.get("bitRate"), list):
                        for br in o["bitRate"]:
                            if isinstance(br, dict) and isinstance(br.get("playAddr"), str):
                                return br["playAddr"]
                    for v in o.values():
                        r = find(v)
                        if r:
                            return r
                elif isinstance(o, list):
                    for v in o:
                        r = find(v)
                        if r:
                            return r
                return None
            r = find(data)
            if r:
                return clean(r)
        except Exception:
            pass

    # 3) 通用 playUrl 字段兜底
    hits = re.findall(r'playUrl"\s*:\s*"(https?:\\?/\\?/[^"]+)"', html)
    if hits:
        return clean(hits[0])
    return None


# ---------------- 下载 ----------------
def download(url: str, cookie_header: str, referer: str, out_path: str):
    import httpx
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Referer": referer,
        "Cookie": cookie_header,
        "Range": "bytes=0-",
    }
    with httpx.stream("GET", url, headers=headers, timeout=90, follow_redirects=True) as r:
        if r.status_code not in (200, 206):
            raise SystemExit(f"下载失败 status={r.status_code} body={r.text[:200]}")
        total = 0
        with open(out_path, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                total += len(chunk)
    return total


# ---------------- 主流程 ----------------
def main():
    # 跨机器兼容：强制 stdout/stderr 用 UTF-8，避免朋友机器 GBK 控制台打印中文崩溃
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="抖音视频下载（cookie 注入 + 无头渲染）")
    ap.add_argument("--url", required=True, help="抖音视频链接 或 video_id")
    ap.add_argument("--cookie-file", required=True, help="明文 cookie 文本文件路径")
    ap.add_argument("--out", default=None, help="输出 mp4 路径，默认 桌面/抖音下载/<id>.mp4")
    ap.add_argument("--keep-cookie", action="store_true", help="保留明文 cookie 文件（默认下载后删除）")
    ap.add_argument("--cleanup-profile", action="store_true", help="下载后删除浏览器配置（默认保留，DPAPI 加密仅本机可读）")
    args = ap.parse_args()

    chrome = find_chrome()
    vid = resolve_video_id(args.url)
    page_url = f"https://www.douyin.com/video/{vid}"
    print(f"[*] video_id={vid}\n[*] page={page_url}\n[*] chrome={chrome}")

    raw = open(args.cookie_file, encoding="utf-8", errors="replace").read()
    cookies = sanitize_cookie(parse_cookie(raw))
    if not cookies:
        raise SystemExit("[失败] cookie 解析后为空，请检查 cookie 文本是否完整")
    cookie_header = "; ".join(f"{n}={v}" for n, v in cookies)
    print(f"[*] cookie 条目: {len(cookies)}（已过滤非 ASCII）")

    profile_dir = os.path.join(tempfile.gettempdir(), "douyin_profile_" + vid)
    if os.path.isdir(profile_dir):
        shutil.rmtree(profile_dir, ignore_errors=True)
    build_profile(cookies, chrome, profile_dir)
    print("[*] profile 构建并校验通过")

    html = render_page(chrome, profile_dir, page_url)
    play = extract_play_url(html)
    if not play:
        raise SystemExit("[失败] 未从页面提取到播放地址（可能 cookie 失效 / 触发风控验证页 / 网络被代理拦截）")
    print(f"[*] 播放地址: {play[:120]}...")

    if not args.out:
        d = os.path.join(os.path.expanduser("~"), "Desktop", "抖音下载")
        os.makedirs(d, exist_ok=True)
        args.out = os.path.join(d, f"抖音_{vid}.mp4")
    total = download(play, cookie_header, page_url, args.out)
    print(f"[*] 下载完成: {args.out} ({total} bytes)")

    # 清理
    if not args.keep_cookie:
        try:
            os.remove(args.cookie_file)
            print("[*] 已删除明文 cookie 文件")
        except Exception:
            pass
    if args.cleanup_profile:
        shutil.rmtree(profile_dir, ignore_errors=True)
        print("[*] 已删除浏览器配置")
    print("[完成] 视频已保存；默认【带抖音水印】，无水印需走第三方去水印服务。")


if __name__ == "__main__":
    main()
