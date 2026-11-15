#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_assemble.py — 星骑士汉化合包装配引擎

把官方 APK 与 LemonLoader 静态层 + ARTEAM 悬浮窗组合成汉化包。
采用直接 ZIP 方式（保留官方二进制 Manifest 和资源表），绕开 apktool 资源编译问题。

流程:
   1. 解包官方 APK（保留原始结构）
   2. 注入 LemonLoader 静态层到 assets/LemonLoader
   3. 加入 ARTEAM 悬浮窗 dex（classes3.dex 等，攻击条UI主体）
   4. 加入悬浮窗 native 库到 lib/{abi}/
   5. patchelf 给 libunity.so 添加 DT_NEEDED（唤醒悬浮窗）
   6. baksmali 反编译 → 注入 UnityPlayerActivity.onCreate 启动入口（KonataInit.init）
   7. axml_patch.py 修补二进制 AndroidManifest，加 SYSTEM_ALERT_WINDOW 权限
   8. 用 zip_pack.py 精确打包
   9. zipalign + apksigner 签名

用法:
    python3 scripts/02_assemble.py <官方APK> <输出APK> [--skip-sign]

退出码:
    0 = 成功
    1 = 失败
"""
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO, "config", "mod.json")

PERMISSION = 'android.permission.SYSTEM_ALERT_WINDOW'


def run(cmd, check=True, capture=False):
    """执行命令"""
    print(f"  $ {' '.join(cmd)}" if isinstance(cmd, list) else f"  $ {cmd}")
    if capture:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if check and r.returncode != 0:
            print(f"[FAIL] 命令失败: {r.stderr}")
            sys.exit(1)
        return r
    r = subprocess.run(cmd)
    if check and r.returncode != 0:
        print(f"[FAIL] 命令失败，退出码 {r.returncode}")
        sys.exit(1)
    return r


def unpack_apk(apk_path: str, dest: str):
    """直接解包 APK（保留所有原始文件，不做 apktool 解码）"""
    print(f"== 解包官方 APK ==")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    with zipfile.ZipFile(apk_path, 'r') as z:
        z.extractall(dest)
    entries = []
    for root, _, files in os.walk(dest):
        for f in files:
            entries.append(os.path.relpath(os.path.join(root, f), dest))
    print(f"  解出 {len(entries)} 个条目")
    return dest


def inject_lemon(dest: str, lemon_cfg: dict):
    """注入 LemonLoader 静态层"""
    print(f"\n== 注入 LemonLoader ==")
    static_dir = os.path.join(REPO, lemon_cfg["static_dir"])
    apk_dest = lemon_cfg["apk_asset_dest"]  # assets/LemonLoader
    target = os.path.join(dest, apk_dest)
    os.makedirs(target, exist_ok=True)

    # 复制 deployment 目录 → assets/LemonLoader/deployment/
    #   必须保留 deployment 这一层！payload.json 里 deploymentFiles 的 path
    #   （Mods/…、UserData/…）是相对 assets/LemonLoader/deployment/ 解析的。
    #   旧实现把 deployment 的内容拍平拷到 assets/LemonLoader/ 根下，运行期
    #   stub 找不到 deployment 目录 → .NET 部署失败、汉化静默不生效。
    deployment_src = os.path.join(static_dir, "deployment")
    if not os.path.isdir(deployment_src):
        print(f"[FAIL] deployment 目录缺失: {deployment_src}（Mods/UserData 部署层，汉化必需）")
        sys.exit(1)
    # 清理历史版本可能拍平留下的根级残留（Mods/UserData 应位于 deployment/ 之下）
    for stale in ("Mods", "UserData"):
        stale_p = os.path.join(target, stale)
        if os.path.isdir(stale_p):
            shutil.rmtree(stale_p)
            print(f"  已清理根级残留: {apk_dest}/{stale}/")
    deployment_dst = os.path.join(target, "deployment")
    if os.path.exists(deployment_dst):
        shutil.rmtree(deployment_dst)
    shutil.copytree(deployment_src, deployment_dst, symlinks=True)
    dep_cnt = sum(len(fs) for _, _, fs in os.walk(deployment_dst))
    print(f"  已注入 deployment 层 → {apk_dest}/deployment/ ({dep_cnt} 个文件)")

    # 复制 payload.json → assets/LemonLoader/payload.json
    #   LemonLoader 新代布局装载清单（formatVersion/runtimeRid/deploymentFiles）。
    #   缺失则 stub 无法定位运行时部署文件，.NET 运行时不会启动 → 汉化静默不生效。
    payload_rel = lemon_cfg.get("payload")
    payload_src = os.path.join(REPO, payload_rel) if payload_rel else os.path.join(static_dir, "payload.json")
    if os.path.isfile(payload_src):
        shutil.copy2(payload_src, os.path.join(target, "payload.json"))
        print(f"  已注入 payload.json ({os.path.getsize(payload_src):,} 字节)")
    else:
        print(f"[FAIL] payload.json 缺失: {payload_src}（LemonLoader 装载清单，汉化必需）")
        sys.exit(1)

    # 复制 runtime/{dotnet,loader} → assets/LemonLoader/runtime/（.NET 运行时载体 + 加载器）
    #   旧实现只拷了 interop，漏了 dotnet/loader，导致 APK 里没有 .NET 运行时，汉化不生效。
    runtime_src = os.path.join(static_dir, "runtime")
    runtime_dst = os.path.join(target, "runtime")
    if not os.path.isdir(runtime_src):
        print(f"[FAIL] runtime 目录缺失: {runtime_src}")
        sys.exit(1)
    os.makedirs(runtime_dst, exist_ok=True)
    for sub in ("dotnet", "loader"):
        src = os.path.join(runtime_src, sub)
        dst = os.path.join(runtime_dst, sub)
        if not os.path.isdir(src):
            print(f"[FAIL] runtime/{sub} 缺失: {src}（.NET 运行时依赖，汉化必需）")
            sys.exit(1)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True)
        cnt = sum(len(fs) for _, _, fs in os.walk(dst))
        print(f"  已注入 runtime/{sub} ({cnt} 个文件)")

    # 复制 interop DLL → assets/LemonLoader/runtime/interop
    #   优先使用 CI 重生成结果（work/interop，与新版 libil2cpp 匹配），
    #   不存在时回退到仓库静态 interop，避免版本更新后 interop 与游戏不匹配。
    gen_interop = os.path.join(REPO, lemon_cfg.get("interop_generated_dir", "work/interop"))
    if os.path.isdir(gen_interop) and os.listdir(gen_interop):
        interop_src = gen_interop
        interop_tag = "重生成"
    else:
        interop_src = os.path.join(runtime_src, "interop")
        interop_tag = "静态"
    interop_dst = os.path.join(runtime_dst, "interop")
    if not os.path.isdir(interop_src):
        print(f"[FAIL] interop 目录缺失: {interop_src}")
        sys.exit(1)
    if os.path.exists(interop_dst):
        shutil.rmtree(interop_dst)
    shutil.copytree(interop_src, interop_dst, symlinks=True)
    count = len(os.listdir(interop_dst))
    print(f"  已注入 interop ({count} 个 DLL, 来源={interop_tag})")

    # 注入 stub libmain → lib/{abi}/libmain.so（装载链入口：启动 .NET 运行时）
    # 说明：LemonLoader 机制要求 APK 的 lib/ 下是 stub，真正的 DMM libmain 放 assets/LemonLoader/。
    #       两者若放反，会出现 .NET 化 il2cpp 无运行时装载 → /memfd:doublemapper 空指针 SIGSEGV。
    libmain_src = os.path.join(REPO, lemon_cfg["libmain"])
    libmain_apk_dest = lemon_cfg.get("apk_libmain_dest", "lib/arm64-v8a/libmain.so")
    libmain_apk_path = os.path.join(dest, libmain_apk_dest)
    if os.path.isfile(libmain_src):
        os.makedirs(os.path.dirname(libmain_apk_path), exist_ok=True)
        shutil.copy2(libmain_src, libmain_apk_path)
        print(f"  已安装 stub libmain → {libmain_apk_dest} ({os.path.getsize(libmain_src):,} 字节)")
    else:
        print(f"[FAIL] stub libmain 缺失: {libmain_src}（装载链入口，无法自动从官方包取得）")
        sys.exit(1)

    # 挪入真实 DMM libmain → assets/LemonLoader/libmain.so（供 stub 运行时装载）
    if lemon_cfg.get("libmain_dmm"):
        dmm_src = os.path.join(REPO, lemon_cfg["libmain_dmm"])
        if os.path.isfile(dmm_src):
            shutil.copy2(dmm_src, os.path.join(target, "libmain.so"))
            print(f"  已挪入 DMM libmain → assets/LemonLoader/libmain.so ({os.path.getsize(dmm_src):,} 字节)")
        else:
            print(f"[FAIL] DMM libmain 缺失: {dmm_src}")
            sys.exit(1)


def inject_float_window(dest: str, float_cfg: dict, abi: str):
    """注入 ARTEAM 悬浮窗
    
    DEX 注入策略：
      官方 APK 有 classes.dex + classes2.dex
      悬浮窗有 classes.dex（攻击条UI主体）+ classes2.dex（补充代码）+ classes3.dex（桥接层）
      三者全部注入，编号为 classes3.dex / classes4.dex / classes5.dex，
      与官方的 classes.dex / classes2.dex 不冲突。
    """
    print(f"\n== 注入 ARTEAM 悬浮窗 ==")
    float_dir = os.path.join(REPO, float_cfg["dex_dir"])

    # 1. 注入悬浮窗全部 dex（编号从 classes3 起，避开官方自带的 classes.dex/classes2.dex）
    #    dex 清单来自 config（float_window.dex_files），不再写死，避免漏注入桥接层。
    float_dexes = float_cfg.get("dex_files", ["classes.dex", "classes2.dex", "classes3.dex"])
    # 确定官方 APK 已有的 dex 编号，从 classes3 开始追加
    existing_dexes = []
    for root, _, files in os.walk(dest):
        for f in files:
            if f.startswith("classes") and f.endswith(".dex"):
                existing_dexes.append(f)
    # 找到下一个可用编号
    next_num = 3
    while f"classes{next_num}.dex" in existing_dexes:
        next_num += 1

    injected = 0
    for i, dex_name in enumerate(float_dexes):
        src = os.path.join(float_dir, dex_name)
        if os.path.isfile(src):
            dst_name = f"classes{next_num + i}.dex"
            shutil.copy2(src, os.path.join(dest, dst_name))
            size_mb = os.path.getsize(src) / (1024 * 1024)
            print(f"  已注入 {dst_name} ({dex_name}, {size_mb:.1f}MB)")
            injected += 1
        else:
            print(f"  [WARN] 悬浮窗 dex 缺失: {dex_name}（跳过）")

    if injected == 0:
        print(f"[FAIL] 悬浮窗 dex 一个都没找到，无法注入攻击条UI")
        sys.exit(1)

    # 2. 加入悬浮窗 native 库
    if float_cfg.get("native_dir"):
        native_dir = os.path.join(REPO, float_cfg["native_dir"])
    else:
        native_dir = os.path.join(float_dir, "lib", abi)
    native_files = float_cfg.get("native_files", [])
    target_lib = os.path.join(dest, "lib", abi)
    os.makedirs(target_lib, exist_ok=True)
    for so in native_files:
        src = os.path.join(native_dir, so)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(target_lib, so))
            print(f"  已加入 {so}")
        else:
            print(f"  [WARN] 缺失 native 库: {so}")


def inject_activity_entry(dest: str):
    """在 UnityPlayerActivity.onCreate 尾部注入 KonataInit.init(this)

    流程：定位含 UnityPlayerActivity 的 dex → baksmali 反编译 → inject.py activity 改 smali
          → smali 回编译 → 替换回解包目录。
    反编译/回编译耗时较长（各约 40s），超时给足。
    """
    print(f"\n== 注入 Activity 启动入口（UnityPlayerActivity.onCreate）==")

    inject_script = os.path.join(REPO, "scripts", "inject.py")
    if not os.path.isfile(inject_script):
        print(f"  [WARN] inject.py 不存在，跳过 Activity 入口注入")
        return

    # 1. 定位解包目录中所有 dex，找出含 UnityPlayerActivity 的
    dexes = []
    for f in os.listdir(dest):
        if f.startswith("classes") and f.endswith(".dex"):
            dexes.append(f)
    if not dexes:
        print(f"  [WARN] 解包目录中无 dex，跳过")
        return

    target_dex = None
    tmp_dir = os.path.join(dest, ".activity_inject_tmp")
    for dex in sorted(dexes):
        dex_path = os.path.join(dest, dex)
        # 用 grep 快速判断字符串是否存在（类定义会含类名字符串）
        r = subprocess.run(["grep", "-qa", "UnityPlayerActivity", dex_path], capture_output=True)
        if r.returncode == 0:
            target_dex = dex
            break

    if not target_dex:
        print(f"  [WARN] 未在任何 dex 中找到 UnityPlayerActivity，跳过 Activity 入口注入")
        return

    print(f"  目标 dex: {target_dex}")

    # 2. 反编译目标 dex
    smali_out = os.path.join(tmp_dir, "smali")
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(smali_out)
    dex_path = os.path.join(dest, target_dex)
    print("  baksmali 反编译中（约 40s）...")
    r = subprocess.run(["baksmali", "disassemble", dex_path, "-o", smali_out],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print(f"  [FAIL] baksmali 反编译失败: {r.stderr.strip()[:200]}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    # 3. 检查 UnityPlayerActivity.smali 是否存在
    act_smali = os.path.join(smali_out, "com", "unity3d", "player", "UnityPlayerActivity.smali")
    if not os.path.isfile(act_smali):
        print(f"  [WARN] 反编译结果中无 UnityPlayerActivity.smali，跳过")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    # 4. 调用 inject.py activity 修改 smali
    print("  inject.py 注入 KonataInit.init 调用...")
    r2 = subprocess.run([sys.executable, inject_script, "activity", tmp_dir, smali_out],
                        capture_output=True, text=True, timeout=30)
    if r2.returncode != 0:
        print(f"  [FAIL] inject.py activity 失败: {r2.stderr.strip()[:200]}\n         {r2.stdout.strip()}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return
    print(f"  {r2.stdout.strip()}")

    # 5. smali 回编译
    patched_dex = os.path.join(tmp_dir, "classes_patched.dex")
    print("  smali 回编译中（约 40s）...")
    r3 = subprocess.run(["smali", "assemble", smali_out, "-o", patched_dex],
                        capture_output=True, text=True, timeout=180)
    if r3.returncode != 0:
        print(f"  [FAIL] smali 回编译失败: {r3.stderr.strip()[:200]}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    if not os.path.isfile(patched_dex):
        print(f"  [FAIL] 回编译产物不存在")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    # 6. 替换回解包目录
    shutil.copy2(patched_dex, os.path.join(dest, target_dex))
    print(f"  [OK] 已用 patched dex 替换 {target_dex}")
    shutil.rmtree(tmp_dir, ignore_errors=True)


def patch_libunity(dest: str, patch_cfg: dict, abi: str):
    """用 patchelf 给 libunity.so 添加 DT_NEEDED"""
    print(f"\n== patchelf 修补 libunity.so ==")
    target_rel = patch_cfg["target"]  # lib/{abi}/libunity.so
    target = os.path.join(dest, target_rel)
    if not os.path.isfile(target):
        # 尝试查找
        for root, _, files in os.walk(os.path.join(dest, "lib")):
            if "libunity.so" in files:
                target = os.path.join(root, "libunity.so")
                print(f"  找到 libunity.so: {target}")
                break
        else:
            print(f"  [WARN] 未找到 libunity.so，跳过 patchelf")
            return

    add_needed = patch_cfg.get("add_needed", [])
    if not add_needed:
        print("  （无 add_needed 配置，跳过）")
        return
    for lib in add_needed:
        # 检查该 lib 是否在目标目录中；缺失即硬失败，避免静默产出"悬浮窗挂载不上"的废包
        lib_path = os.path.join(dest, "lib", abi, lib)
        if not os.path.isfile(lib_path):
            print(f"[FAIL] {lib} 不在 lib/{abi}/ 中，无法建立 DT_NEEDED 链（悬浮窗将无法挂载）")
            sys.exit(1)
        # patchelf 失败必须中止；这是上次崩溃包的静默根因之一。
        r = run(["patchelf", "--add-needed", lib, target], capture=True, check=False)
        if r.returncode != 0:
            print(f"[FAIL] patchelf 添加 DT_NEEDED 失败 ({lib}): {r.stderr.strip()[:200]}")
            sys.exit(1)
        print(f"  已添加 DT_NEEDED: {lib}")

    # 校验：readelf -d 的动态段里应出现全部 add_needed
    rd = run(["readelf", "-d", target], capture=True, check=False)
    missing = [lib for lib in add_needed if lib not in rd.stdout]
    if missing:
        print(f"[FAIL] DT_NEEDED 校验失败，缺少: {missing}")
        sys.exit(1)
    print(f"  DT_NEEDED 校验通过: {add_needed}")


def patch_manifest_binary(dest: str):
    """给二进制 AndroidManifest.xml 添加 SYSTEM_ALERT_WINDOW 权限

    直接调用 axml_patch.py（已验证的二进制 AXML 修补器），
    不依赖 apktool，避免资源重编译问题。
    """
    print(f"\n== 修补 AndroidManifest（二进制 AXML）==")
    manifest_path = os.path.join(dest, "AndroidManifest.xml")
    if not os.path.isfile(manifest_path):
        print(f"  [WARN] 无 AndroidManifest.xml，跳过")
        return

    # 检查是否为二进制格式
    with open(manifest_path, 'rb') as f:
        head = f.read(4)

    if head != b'\x03\x00\x08\x00':
        # 文本格式，用简单文本注入
        txt = open(manifest_path, encoding='utf-8').read()
        if PERMISSION not in txt:
            anchor = '<uses-permission'
            idx = txt.find(anchor)
            line = f'    <uses-permission android:name="{PERMISSION}"/>\n'
            if idx == -1:
                idx = txt.find('<application')
                if idx != -1:
                    txt = txt[:idx] + line + txt[idx:]
            else:
                bol = txt.rfind('\n', 0, idx) + 1
                txt = txt[:bol] + line + txt[bol:]
            open(manifest_path, 'w', encoding='utf-8').write(txt)
            print(f"  已在文本 manifest 中添加 {PERMISSION}")
        else:
            print(f"  {PERMISSION} 已存在，跳过")
        return

    # 二进制格式，调用 axml_patch.py
    axml_script = os.path.join(REPO, "scripts", "axml_patch.py")
    if not os.path.isfile(axml_script):
        print(f"  [WARN] axml_patch.py 不存在，跳过 manifest 修补")
        return

    print("  Manifest 为二进制格式，调用 axml_patch.py...")
    r = subprocess.run(
        [sys.executable, axml_script, manifest_path, PERMISSION],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        print(f"  [OK] axml_patch.py 执行成功: {r.stdout.strip()}")
    else:
        print(f"  [FAIL] axml_patch.py 失败: {r.stderr.strip()}")
        print(f"         输出: {r.stdout.strip()}")
        # 不直接退出，允许继续构建，但明确标记风险
        print(f"         [WARN] 权限修补失败，悬浮窗可能无法正常显示，请检查")


def sign_apk(apk_path: str, signing_cfg: dict, skip_sign: bool):
    """zipalign + apksigner 签名"""
    if skip_sign:
        print("\n== 跳过签名 ==")
        return

    print(f"\n== zipalign + 签名 ==")
    # zipalign
    aligned = apk_path + ".aligned"
    zipalign_args = signing_cfg.get("zipalign", "-p -f -v 4").split()
    run(["zipalign"] + zipalign_args + [apk_path, aligned])
    print(f"  zipalign 完成: {aligned}")

    # apksigner 签名
    keystore = os.path.join(REPO, signing_cfg["keystore"])
    alias = signing_cfg["alias"]
    pass_env = os.environ.get("TSK_KS_PASS", "")
    if not pass_env:
        print("[FAIL] 未设置 TSK_KS_PASS 环境变量，无法签名")
        sys.exit(1)
    if not os.path.isfile(keystore):
        print(f"[FAIL] 签名 {keystore} 不存在: {keystore}")
        sys.exit(1)

    v1 = "true" if signing_cfg.get("v1") else "false"
    v2 = "true" if signing_cfg.get("v2") else "false"
    v3 = "true" if signing_cfg.get("v3") else "false"

    run([
        "apksigner", "sign",
        "--ks", keystore,
        "--ks-key-alias", alias,
        "--ks-pass", f"env:TSK_KS_PASS",
        "--key-pass", f"env:TSK_KS_PASS",
        "--v1-signing-enabled", v1,
        "--v2-signing-enabled", v2,
        "--v3-signing-enabled", v3,
        "--out", apk_path,
        aligned
    ])
    # 清理临时文件
    if os.path.isfile(aligned):
        os.remove(aligned)
    print(f"  签名完成: {apk_path}")

    # 验证签名
    r = run(["apksigner", "verify", "--print-certs", apk_path], capture=True, check=False)
    if r.returncode == 0:
        print("  签名验证通过 ✅")
    else:
        print(f"  [WARN] 签名验证未通过: {r.stderr}")


def verify_unpacked(dest: str, cfg: dict, abi: str):
    """出厂自检：确保装载链三要素正确，不合格直接拒绝打包。

    校验项：
      1) lib/{abi}/libmain.so 是 stub（约 10KB，非 DMM 原版 ~1.27MB）
      2) assets/LemonLoader/libmain.so 是 DMM 原版（~1.27MB）
      3) lib/{abi}/libunity.so 的动态段含全部 add_needed
      4) 悬浮窗 native 库齐全
    """
    print(f"\n== 出厂自检 ==")
    ok = True

    stub_src = os.path.join(REPO, cfg["lemon"]["libmain"])
    dmm_src = os.path.join(REPO, cfg["lemon"].get("libmain_dmm", ""))

    lib_stub = os.path.join(dest, "lib", abi, "libmain.so")
    asset_dmm = os.path.join(dest, "assets", "LemonLoader", "libmain.so")

    # 1) lib/ 下 libmain 应与仓库 stub 完全一致
    if os.path.isfile(lib_stub) and os.path.isfile(stub_src):
        if files_equal(lib_stub, stub_src):
            print(f"  [OK] lib/{abi}/libmain.so == stub ({os.path.getsize(lib_stub):,} 字节)")
        else:
            print(f"  [FAIL] lib/{abi}/libmain.so 与 stub 不一致（装载链位置可能放反）")
            ok = False
    else:
        print(f"  [FAIL] 缺少 lib/{abi}/libmain.so")
        ok = False

    # 2) assets/LemonLoader/libmain.so 应与 DMM 原版一致
    if os.path.isfile(asset_dmm) and dmm_src and os.path.isfile(dmm_src):
        if files_equal(asset_dmm, dmm_src):
            print(f"  [OK] assets/LemonLoader/libmain.so == DMM 原版 ({os.path.getsize(asset_dmm):,} 字节)")
        else:
            print(f"  [FAIL] assets/LemonLoader/libmain.so 与 DMM 原版不一致")
            ok = False
    else:
        print(f"  [FAIL] 缺少 assets/LemonLoader/libmain.so")
        ok = False

    # 3) libunity 的 DT_NEEDED
    add_needed = cfg["patch_libs"].get("add_needed", [])
    libunity = os.path.join(dest, cfg["patch_libs"]["target"])
    if os.path.isfile(libunity) and add_needed:
        rd = run(["readelf", "-d", libunity], capture=True, check=False)
        missing = [lib for lib in add_needed if lib not in rd.stdout]
        if missing:
            print(f"  [FAIL] libunity DT_NEEDED 缺少: {missing}")
            ok = False
        else:
            print(f"  [OK] libunity DT_NEEDED 完整: {add_needed}")

    # 4) LemonLoader 静态层完整性（缺任一 => 汉化运行时无法启动，必然静默不生效）
    lemon_cfg = cfg["lemon"]
    ll_dir = os.path.join(dest, lemon_cfg["apk_asset_dest"])
    must_files = [os.path.join(ll_dir, "payload.json")]
    must_dirs = [
        os.path.join(ll_dir, "runtime", "dotnet"),
        os.path.join(ll_dir, "runtime", "loader"),
        os.path.join(ll_dir, "runtime", "interop"),
        os.path.join(ll_dir, "deployment"),
    ]
    for f in must_files:
        if os.path.isfile(f):
            print(f"  [OK] LemonLoader 装载清单存在: {os.path.relpath(f, dest)}")
        else:
            print(f"  [FAIL] LemonLoader 缺失装载清单: {os.path.relpath(f, dest)}")
            ok = False
    src_static = os.path.join(REPO, lemon_cfg["static_dir"])
    for d in must_dirs:
        rel = os.path.relpath(d, ll_dir)
        src_d = os.path.join(src_static, rel)
        if not os.path.isdir(d):
            print(f"  [FAIL] LemonLoader 缺失目录: assets/LemonLoader/{rel}")
            ok = False
            continue
        n_packed = sum(len(fs) for _, _, fs in os.walk(d))
        n_src = sum(len(fs) for _, _, fs in os.walk(src_d)) if os.path.isdir(src_d) else -1
        # interop 允许来自 CI 重生成，不做数量一致校验；其余目录须与静态层一致
        if rel in ("runtime/dotnet", "runtime/loader", "deployment") and n_src >= 0 and n_packed != n_src:
            print(f"  [FAIL] assets/LemonLoader/{rel} 文件数不符: 包内={n_packed} 静态层={n_src}")
            ok = False
        else:
            print(f"  [OK] assets/LemonLoader/{rel} ({n_packed} 个文件)")

    # 5) 悬浮窗 native 库
    for so in cfg["float_window"].get("native_files", []):
        if not os.path.isfile(os.path.join(dest, "lib", abi, so)):
            print(f"  [FAIL] 悬浮窗 native 库缺失: {so}")
            ok = False

    if not ok:
        print("\n[FAIL] 出厂自检未通过，已中止打包（避免产出崩溃/功能缺失包）")
        sys.exit(1)
    print("  [OK] 出厂自检全部通过 ✅")


def files_equal(a: str, b: str) -> bool:
    """比较两个文件是否逐字节相同：先比大小，再流式分块比对（大文件友好）"""
    if not (os.path.isfile(a) and os.path.isfile(b)):
        return False
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca = fa.read(1 << 20)
            cb = fb.read(1 << 20)
            if ca != cb:
                return False
            if not ca:
                return True


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    apk_path = sys.argv[1]
    output_apk = sys.argv[2]
    skip_sign = "--skip-sign" in sys.argv

    cfg = json.load(open(CONFIG, encoding="utf-8"))
    abi = cfg["game"]["abi"]

    print("== 02_assemble ==")
    print(f"输入 APK: {apk_path}")
    print(f"输出 APK: {output_apk}")
    print(f"架构    : {abi}")

    if not os.path.isfile(apk_path):
        print(f"[FAIL] 输入 APK 不存在: {apk_path}")
        return 1

    work_dir = os.path.join(REPO, "work", "assemble")
    unpack_dir = os.path.join(work_dir, "unpacked")

    # 1. 解包
    unpack_apk(apk_path, unpack_dir)

    # 2. 注入 LemonLoader
    inject_lemon(unpack_dir, cfg["lemon"])

    # 3. 注入悬浮窗
    inject_float_window(unpack_dir, cfg["float_window"], abi)

    # 4. patchelf 修补 libunity.so
    patch_libunity(unpack_dir, cfg["patch_libs"], abi)

    # 4.5 注入 Activity 启动入口（UnityPlayerActivity.onCreate → KonataInit.init）
    inject_activity_entry(unpack_dir)

    # 5. 修补 manifest
    patch_manifest_binary(unpack_dir)
    # 5.5 出厂自检（不合格直接拒绝打包，避免产出崩溃/功能缺失包）
    verify_unpacked(unpack_dir, cfg, abi)
    # 6. 打包
    print(f"\n== 打包 APK ==")
    run([sys.executable, os.path.join(REPO, "scripts", "zip_pack.py"), unpack_dir, output_apk])

    # 7. 签名
    sign_apk(output_apk, cfg["signing"], skip_sign)

    # 生成构建报告
    report = {
        "output": output_apk,
        "size": os.path.getsize(output_apk),
        "source_apk": apk_path,
        "abi": abi,
        "components": ["LemonLoader", "ARTEAM悬浮窗(全量DEX)", "libunity patched", "Activity启动入口(KonataInit)", "SYSTEM_ALERT_WINDOW权限"],
        "signed": not skip_sign,
    }
    report_path = os.path.join(REPO, "dist", "build_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    json.dump(report, open(report_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\n== 装配完成 ✅ ==")
    print(f"输出: {output_apk}")
    print(f"报告: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())