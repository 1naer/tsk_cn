#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inject.py — 在 apktool 解包目录上做最小必要改动

子命令:
    manifest  <unpack目录>              给 AndroidManifest.xml 加 SYSTEM_ALERT_WINDOW 权限
    activity  <unpack目录> <smali根目录>  在 UnityPlayerActivity.onCreate 尾部插入 KonataInit.init(this)
    check     <unpack目录>              注入结果自检

设计原则:
  - 幂等：重复执行不会重复插入
  - 最小改动：只加一条权限、只加 6 行 smali
  - 失败即报错，绝不静默跳过
"""
import os
import re
import sys

PERMISSION = 'android.permission.SYSTEM_ALERT_WINDOW'
ACTIVITY_SMALI = 'com/unity3d/player/UnityPlayerActivity.smali'
INIT_CALL = ('invoke-static {p0}, Lcom/android/support/KonataInit;->'
             'init(Landroid/content/Context;)V')
MARKER = 'KonataInit'


def find_manifest(root):
    p = os.path.join(root, 'AndroidManifest.xml')
    # apktool 输出的是文本 XML；若为二进制则说明没解包
    if os.path.isfile(p) and open(p, 'rb').read(4) != b'\x03\x00\x08\x00':
        return p
    return None


def cmd_manifest(root):
    p = find_manifest(root)
    if not p:
        print('[FAIL] 未找到文本 AndroidManifest.xml（apktool 解包不完整？）')
        return 1
    txt = open(p, encoding='utf-8').read()

    if PERMISSION in txt:
        print('[SKIP] SYSTEM_ALERT_WINDOW 权限已存在')
    else:
        # 插到第一个 uses-permission 之前，保证在 <application> 之前
        anchor = '<uses-permission'
        idx = txt.find(anchor)
        line = '    <uses-permission android:name="%s"/>\n' % PERMISSION
        if idx == -1:
            idx = txt.find('<application')
            if idx == -1:
                print('[FAIL] 找不到 <application>，manifest 结构异常')
                return 1
            txt = txt[:idx] + line + txt[idx:]
        else:
            # 找到该行行首
            bol = txt.rfind('\n', 0, idx) + 1
            txt = txt[:bol] + line + txt[bol:]
        open(p, 'w', encoding='utf-8').write(txt)
        print('[ OK ] 已插入 SYSTEM_ALERT_WINDOW')

    # 检查有无 android:name 为 com.android.support.Launcher 的 service（历史崩溃源）
    if re.search(r'<service[^>]*com\.android\.support\.Launcher', txt):
        print('[WARN] manifest 中声明了 com.android.support.Launcher Service —— 会导致 NPE 闪退，应删除')
        return 1
    print('[ OK ] 无 Launcher Service 声明')
    return 0


def find_activity_smali(unpack_root, smali_root):
    for cand in (smali_root, os.path.join(unpack_root, 'smali_classes2'),
                 os.path.join(unpack_root, 'smali_classes3')):
        p = os.path.join(cand, ACTIVITY_SMALI)
        if os.path.isfile(p):
            return p
    return None


def cmd_activity(unpack_root, smali_root):
    p = find_activity_smali(unpack_root, smali_root)
    if not p:
        print(f'[FAIL] 找不到 {ACTIVITY_SMALI}')
        return 1
    print(f'目标: {p}')
    src = open(p, encoding='utf-8').read()

    if MARKER in src:
        print('[SKIP] KonataInit 调用已存在')
        return 0

    # 定位 onCreate 方法
    m = re.search(r'(\.method\s+(?:protected|public)\s+onCreate\(Landroid/os/Bundle;\)V\s*\n)', src)
    if not m:
        print('[FAIL] 找不到 onCreate(Landroid/os/Bundle;)V —— Unity 版本可能不同，需人工处理')
        return 1

    start = m.end()
    end_m = re.search(r'\n\.end method', src[start:])
    if not end_m:
        print('[FAIL] onCreate 方法未闭合')
        return 1
    body_end = start + end_m.start()  # 指向 ".end method" 前的换行

    body = src[start:body_end]

    # 找最后一个 return-void（onCreate 正常出口）
    rv = body.rfind('return-void')
    if rv == -1:
        print('[FAIL] onCreate 内无 return-void')
        return 1

    # invoke-static {p0} 只用 p0，Unity 的 onCreate 至少 .registers 3，无需新增寄存器
    indent = '    '
    inject = (f'{indent}\n'
              f'{indent}# ---- KonataInit: ARTEAM 悬浮窗延迟启动（main thread, 6s）----\n'
              f'{indent}{INIT_CALL}\n'
              f'{indent}\n'
              f'{indent}')
    new_body = body[:rv] + inject + body[rv:]
    src = src[:start] + new_body + src[body_end:]
    open(p, 'w', encoding='utf-8').write(src)
    print('[ OK ] 已在 onCreate 尾部插入 KonataInit.init')
    print('       插入点: 最后一个 return-void 之前（即 super 生命周期走完后）')
    return 0


def cmd_check(root):
    p = find_manifest(root)
    if not p:
        print('[FAIL] 无文本 manifest')
        return 1
    txt = open(p, encoding='utf-8').read()
    ok = PERMISSION in txt
    print(f"[{'OK' if ok else 'FAIL'}] manifest 权限")
    pkg = re.search(r'package="([^"]+)"', txt)
    print(f"      package = {pkg.group(1) if pkg else '?'}")
    return 0 if ok else 1


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == 'manifest':
        return cmd_manifest(sys.argv[2])
    if cmd == 'activity':
        if len(sys.argv) < 4:
            print(__doc__)
            return 2
        return cmd_activity(sys.argv[2], sys.argv[3])
    if cmd == 'check':
        return cmd_check(sys.argv[2])
    print(f'未知子命令: {cmd}')
    return 2


if __name__ == '__main__':
    sys.exit(main())