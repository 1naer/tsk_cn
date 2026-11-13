#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
01_verify_input.py — 输入 APK 完整性 & 可做宿主性校验

用法:
    python3 scripts/01_verify_input.py <apk路径> [--strict]

检查项:
  1. ZIP 结构可完整读取（等价 unzip -t，逐条目校验 CRC）
  2. 必需条目存在
  3. assets/LemonLoader 是否已存在（判断是官包还是别人改过的包）
  4. 提取 binary AndroidManifest 的 package / versionName / versionCode
  5. 计算 SHA-256 / MD5 / 大小，写入 work/input_report.json

退出码: 0 = 可作宿主, 1 = 不可作宿主
"""
import hashlib
import json
import os
import re
import struct
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO, "config", "mod.json")


# ---------------- 极简 AXML 解析（只取 manifest 根节点属性） ----------------
def parse_axml_package(data: bytes) -> dict:
    out = {}
    try:
        if len(data) < 8 or struct.unpack("<H", data[0:2])[0] != 0x0003:
            return out
        # 跳过文件头
        pos = 8
        # 遍历 chunk，找到 RES_XML_START_ELEMENT (0x0102) 的第一层（manifest）
        strpool = None
        strpool_offsets = None
        while pos + 8 <= len(data):
            ctype, hsize, csize = struct.unpack("<HHI", data[pos:pos + 8])
            if csize < 8 or pos + csize > len(data):
                break
            if ctype == 0x0001:  # string pool
                chunk = data[pos:pos + csize]
                scount, _sstyle, flags, sstart, _sstyle_start = struct.unpack("<IIIII", chunk[8:28])
                utf8 = bool(flags & (1 << 8))
                offs = struct.unpack("<%dI" % scount, chunk[28:28 + 4 * scount])
                base = sstart
                strs = []
                for o in offs:
                    p = base + o
                    if utf8:
                        n = chunk[p]
                        p += 2 if n & 0x80 else 1
                        n = chunk[p]
                        if n & 0x80:
                            n = ((n & 0x7F) << 8) | chunk[p + 1]
                            p += 1
                        p += 1
                        strs.append(chunk[p:p + n].decode("utf-8", "replace"))
                    else:
                        n = struct.unpack("<H", chunk[p:p + 2])[0]
                        p += 2
                        if n & 0x8000:
                            n = ((n & 0x7FFF) << 16) | struct.unpack("<H", chunk[p:p + 2])[0]
                            p += 2
                        strs.append(chunk[p:p + n * 2].decode("utf-16-le", "replace"))
                strpool = strs
                pos += csize
                continue
            if ctype == 0x0102 and strpool is not None:  # START_ELEMENT
                chunk = data[pos:pos + csize]
                _ns, name_i, attr_start, attr_size, attr_count = struct.unpack("<IIHHH", chunk[16:32])
                if not out and name_i < len(strpool) and strpool[name_i] == "manifest":
                    for i in range(attr_count):
                        a = 32 + i * attr_size
                        _ans, aname, rawv, _t, _d = struct.unpack("<IIIHB", chunk[a:a + 15])
                        # 跳过 3 字节 padding 后是 value
                        val = struct.unpack("<I", chunk[a + 16:a + 20])[0]
                        key = strpool[aname] if aname < len(strpool) else "?"
                        if key == "package":
                            out["package"] = strpool[val] if val < len(strpool) else None
                        elif key == "versionName":
                            try:
                                out["versionName"] = strpool[rawv] if rawv != 0xFFFFFFFF else strpool[val]
                            except Exception:
                                pass
                        elif key == "versionCode":
                            out["versionCode"] = val
                    break
            pos += csize
    except Exception as e:  # 解析失败不影响主流程
        out["_axml_error"] = str(e)
    return out


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/01_verify_input.py <apk路径> [--strict]")
        return 2
    apk = sys.argv[1]
    strict = "--strict" in sys.argv

    if not os.path.isfile(apk):
        print(f"[FAIL] 文件不存在: {apk}")
        return 1

    cfg = json.load(open(CONFIG, encoding="utf-8"))
    required = cfg["required_entries"]

    size = os.path.getsize(apk)
    print(f"== 01_verify_input ==\n文件: {apk}\n大小: {size:,} 字节 ({size/1048576:.1f} MiB)")

    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    with open(apk, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            sha256.update(b)
            md5.update(b)
    print(f"SHA-256: {sha256.hexdigest()}\nMD5    : {md5.hexdigest()}")

    report = {
        "apk": os.path.abspath(apk),
        "size": size,
        "sha256": sha256.hexdigest(),
        "md5": md5.hexdigest(),
        "checks": {},
        "ok": False,
    }

    # ---- ZIP / CRC ----
    bad_crc = []
    names = []
    try:
        with zipfile.ZipFile(apk) as z:
            names = z.namelist()
            for info in z.infolist():
                try:
                    with z.open(info) as fh:
                        while fh.read(1 << 20):
                            pass
                except Exception as e:
                    bad_crc.append(f"{info.filename}: {e}")
    except Exception as e:
        print(f"[FAIL] ZIP 无法读取: {e}")
        report["checks"]["zip"] = {"ok": False, "error": str(e)}
        _write(report)
        return 1

    report["checks"]["zip"] = {"ok": not bad_crc, "entries": len(names), "crc_errors": bad_crc[:10]}
    print(f"ZIP   : {len(names)} 条目, CRC 错误 {len(bad_crc)}")
    if bad_crc:
        for b in bad_crc[:5]:
            print(f"   - {b}")

    # ---- 必需条目 ----
    missing = [r for r in required if r not in names]
    report["checks"]["required_entries"] = {"ok": not missing, "missing": missing}
    print(f"必需条目: {'齐全' if not missing else '缺失 -> ' + ', '.join(missing)}")

    # ---- 是否已是改装包 ----
    already = [n for n in names if n.startswith("assets/LemonLoader")]
    report["checks"]["already_modded"] = {"count": len(already), "sample": already[:5]}
    if already:
        print(f"[注意] 该包已含 assets/LemonLoader（{len(already)} 条目），不是纯净官包")

    # ---- 悬浮窗残留 ----
    float_leftover = [n for n in names if re.search(r"classes\d*\.dex$", n)]
    report["checks"]["dex_count"] = len(float_leftover)
    print(f"dex  : {len(float_leftover)} 个 -> {float_leftover}")

    # ---- Manifest ----
    try:
        with zipfile.ZipFile(apk) as z:
            man = parse_axml_package(z.read("AndroidManifest.xml"))
    except Exception as e:
        man = {"_error": str(e)}
    report["manifest"] = man
    print(f"Manifest: {json.dumps(man, ensure_ascii=False)}")

    # ---- 结论 ----
    ok = (not bad_crc) and (not missing)
    if strict and already:
        ok = False
    report["ok"] = ok
    _write(report)
    print(f"\n== 结论: {'可作宿主 ✅' if ok else '不可作宿主 ❌'} ==")
    return 0 if ok else 1


def _write(report):
    out = os.path.join(REPO, "work", "input_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"报告已写入: {out}")


if __name__ == "__main__":
    sys.exit(main())
