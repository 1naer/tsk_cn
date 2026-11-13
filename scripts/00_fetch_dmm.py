#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
00_fetch_dmm.py — DMM 官方源更新检测 + APK 下载

原理（复刻第三方站点的下载机制）：
  1. 通过 Vercel 代理调用 DMM 官方 API，拿目标游戏的应用详情
  2. 从返回中提取 app_version_name / app_version_code / download_url
  3. 与本地 last_version.json 记录比对，有更新才下载 APK
  4. 记录版本信息供后续合包流程使用

HTTP 层优先使用 curl（对代理/CDN 的 TLS 兼容性更好），urllib 作后备。

用法:
    python3 scripts/00_fetch_dmm.py [--app-id 705566] [--force] [--output work/official.apk]

退出码:
    0 = 有新版本并已下载 / 无更新（--force 时强制下载）
    1 = 错误
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO, "config", "mod.json")
VERSION_FILE = os.path.join(REPO, "work", "last_version.json")

# DMM 官方 API 代理（第三方站点的公开代理，也可用自建代理替换）
DMM_API = "https://api.store.games.dmm.com"
VERCEL_PROXY = "https://vercel.ntr.best/proxy"

# 目标游戏 app_id（ティンクルスターナイツX for Android）
DEFAULT_APP_ID = "705566"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CURL_RETRIES = 3
CURL_RETRY_WAIT = 3


def _have_curl() -> bool:
    return shutil.which("curl") is not None


def _curl_get(url: str, timeout: int = 60, retries: int = CURL_RETRIES) -> bytes:
    """用 curl 抓 URL，带重试"""
    # -w 用 \n__CODE__<code> 格式，便于稳定解析
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", UA,
           "-w", "\n__CODE__%{http_code}", url]
    for attempt in range(1, retries + 1):
        r = subprocess.run(cmd, capture_output=True, text=True)
        out = r.stdout
        marker = "__CODE__"
        if marker in out:
            code_str = out.rsplit(marker, 1)[1].strip()
            body = out.rsplit("\n__CODE__", 1)[0]
            try:
                code = int(code_str)
            except ValueError:
                code = 0
            if code == 200:
                return body.encode("utf-8")
            if attempt < retries:
                import time
                time.sleep(CURL_RETRY_WAIT)
                continue
            raise urllib.error.HTTPError(url, code, f"HTTP {code}", None, None)
        if attempt < retries:
            import time
            time.sleep(CURL_RETRY_WAIT)
            continue
        raise RuntimeError(f"curl 请求失败（重试 {retries} 次）: {r.stderr[:100]}")
    raise RuntimeError("curl 请求失败")


def fetch_app_detail(app_id: str) -> dict:
    """通过代理调 DMM 官方 freeapp API，返回应用详情"""
    target = f"{DMM_API}/freeapp/{app_id}"
    proxy_url = f"{VERCEL_PROXY}/{urllib.parse.quote(target, safe='')}"

    data = None
    if _have_curl():
        body = _curl_get(proxy_url, timeout=60)
        data = json.loads(body.decode("utf-8"))
    else:
        # urllib 后备（部分环境下 TLS 握手可能失败，故优先 curl）
        req = urllib.request.Request(proxy_url, headers={
            "User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))

    info = data.get("free_appinfo", data)
    return info


def load_last_version() -> dict:
    """读取上次记录的版本信息"""
    if os.path.isfile(VERSION_FILE):
        try:
            return json.load(open(VERSION_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_version(info: dict, apk_path: str = None):
    """记录当前版本信息"""
    os.makedirs(os.path.dirname(VERSION_FILE), exist_ok=True)
    record = {
        "app_id": info.get("app_id"),
        "app_name": info.get("app_name"),
        "version_name": info.get("app_version_name"),
        "version_code": info.get("app_version_code"),
        "package_name": info.get("package_name"),
        "file_size": info.get("file_size"),
    }
    if apk_path:
        record["apk_path"] = os.path.abspath(apk_path)
        record["apk_sha256"] = compute_sha256(apk_path)
    json.dump(record, open(VERSION_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"版本记录已写入: {VERSION_FILE}")


def compute_sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def download_apk(url: str, dest: str) -> None:
    """下载 APK 文件（优先 curl，支持重试与断点续传）"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"下载 APK: {url}")

    tmp_dest = dest + ".part"
    if os.path.exists(tmp_dest):
        os.remove(tmp_dest)

    if _have_curl():
        # curl 下载，带重试；-C - 断点续传
        for attempt in range(1, CURL_RETRIES + 1):
            r = subprocess.run(
                ["curl", "-sL", "--max-time", "600", "-A", UA,
                 "-C", "-", "-o", tmp_dest, url],
                capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(tmp_dest):
                break
            print(f"  [重试 {attempt}] curl 返回码 {r.returncode}")
            import time
            time.sleep(CURL_RETRY_WAIT)
        else:
            raise RuntimeError(f"curl 下载失败（重试 {CURL_RETRIES} 次）")
    else:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=600) as resp, open(tmp_dest, "wb") as f:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    print(f"  下载进度: {downloaded / total * 100:.1f}% ({downloaded:,}/{total:,})", end="\r")
            print()

    os.replace(tmp_dest, dest)
    print(f"下载完成: {dest} ({os.path.getsize(dest):,} 字节)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-id", default=DEFAULT_APP_ID, help="DMM app_id（默认 705566）")
    ap.add_argument("--force", action="store_true", help="强制下载，忽略版本比对")
    ap.add_argument("--output", default=os.path.join(REPO, "work", "official.apk"), help="APK 输出路径")
    args = ap.parse_args()

    print("== 00_fetch_dmm ==")
    print(f"app_id: {args.app_id}")

    # 1. 拉取应用详情
    try:
        info = fetch_app_detail(args.app_id)
    except urllib.error.HTTPError as e:
        print(f"[FAIL] API 请求失败: HTTP {e.code} - {e}")
        return 1
    except Exception as e:
        print(f"[FAIL] API 请求异常: {e}")
        return 1

    ver_name = info.get("app_version_name", "?")
    ver_code = info.get("app_version_code", "?")
    pkg = info.get("package_name", "?")
    download_url = info.get("download_url")
    file_size = info.get("file_size", "?")

    print(f"应用名 : {info.get('app_name')}")
    print(f"版本   : {ver_name} (code {ver_code})")
    print(f"包名   : {pkg}")
    print(f"大小   : {file_size} MB")

    if not download_url:
        print("[FAIL] 未获取到 download_url，可能该应用不可下载")
        return 1

    # 2. 版本比对
    last = load_last_version()
    last_ver = last.get("version_name", "")
    if not args.force and last_ver == ver_name:
        print(f"\n== 无更新：当前版本 {last_ver} 与上次记录一致，跳过下载 ==")
        # 输出已有 APK 路径供后续使用
        existing = last.get("apk_path")
        if existing and os.path.isfile(existing):
            print(f"使用已有 APK: {existing}")
            return 0
        else:
            print("[注意] 无本地 APK，仍需下载")

    # 3. 下载
    download_apk(download_url, args.output)

    # 4. 记录版本
    save_version(info, args.output)
    print(f"\n== 检测到新版本 {last_ver or '(无记录)'} -> {ver_name}，下载完成 ✅ ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
