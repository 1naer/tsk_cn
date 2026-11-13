#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zip_pack.py — 把解包目录精确打包成 APK（不经过 apktool 资源重建）

适用于"只改 dex / assets / lib、不改资源"的合包场景，保留官方原始
resources.arsc、二进制 AndroidManifest.xml，彻底绕开 apktool 的资源编译问题。

- 条目名用相对路径（不带 ./ 前缀）
- .so/.dex/.arsc 用 STORED（不压缩），其余 DEFLATED
- 自动跳过 META-INF（旧签名）
"""
import os
import sys
import zipfile

STORED_EXT = {'.so', '.dex', '.arsc', '.mp3', '.ogg', '.bundle'}
# apktool / 构建产物排除
EXCLUDE_DIRS = {'build'}
EXCLUDE_FILES = {'apktool.yml', 'apktool.json'}


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src = sys.argv[1]
    dst = sys.argv[2]
    if os.path.exists(dst):
        os.remove(dst)

    count = 0
    with zipfile.ZipFile(dst, 'w', allowZip64=True) as z:
        for root, dirs, files in os.walk(src):
            dirs.sort()
            # 跳过排除目录
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for fn in sorted(files):
                if fn.startswith('.') or fn in EXCLUDE_FILES:
                    continue
                if fn.endswith('.orig'):
                    continue
                full = os.path.join(root, fn)
                arc = os.path.relpath(full, src).replace(os.sep, '/')
                if arc.startswith('META-INF/'):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                comp = zipfile.ZIP_STORED if ext in STORED_EXT else zipfile.ZIP_DEFLATED
                zi = zipfile.ZipInfo(arc, date_time=(2024, 1, 1, 0, 0, 0))
                zi.compress_type = comp
                zi.external_attr = 0o644 << 16
                if ext in STORED_EXT:
                    zi.external_attr = 0o755 << 16
                with open(full, 'rb') as f:
                    z.writestr(zi, f.read())
                count += 1
    print(f'已打包 {count} 个条目 -> {dst}')
    print(f'大小: {os.path.getsize(dst):,} 字节')
    return 0


if __name__ == '__main__':
    sys.exit(main())