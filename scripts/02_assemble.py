#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_assemble.py — 星骑士汉化合包装配引擎

把官方 APK 与 LemonLoader 静态层 + ARTEAM 悬浮窗组合成汉化包。
采用直接 ZIP 方式（保留官方二进制 Manifest 和资源表），绕开 apktool 资源编译问题。

流程:
  1. 解包官方 APK（保留原始结构）
  2. 注入 LemonLoader 静态层到 assets/LemonLoader
  3. 加入 ARTEAM 悬浮窗 dex（classes3.dex）
  4. 加入悬浮窗 native 库到 lib/{abi}/
  5. patchelf 给 libunity.so 添加 DT_NEEDED（唤醒悬浮窗）
  6. 尝试给 AndroidManifest 添加 SYSTEM_ALERT_WINDOW 权限（二进制 AXML 修补）
  7. 用 zip_pack.py 精确打包
  8. zipalign + apksigner 签名

用法:
    python3 scripts/02_assemble.py <官方APK> <输出APK> [--skip-sign]

退出码:
    0 = 成功
    1 = 失败
"""
import json
import os
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

    # 复制 deployment 文件
    deployment_src = os.path.join(static_dir, "deployment")
    if os.path.isdir(deployment_src):
        # 复制整个 deployment 目录内容到 assets/LemonLoader
        for item in os.listdir(deployment_src):
            src = os.path.join(deployment_src, item)
            dst = os.path.join(target, item)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        print(f"  已注入 deployment 层")

    # 复制 runtime/interop DLL 到 assets/LemonLoader/runtime/interop
    runtime_src = os.path.join(static_dir, "runtime")
    if os.path.isdir(runtime_src):
        runtime_dst = os.path.join(target, "runtime")
        os.makedirs(runtime_dst, exist_ok=True)
        interop_src = os.path.join(runtime_src, "interop")
        interop_dst = os.path.join(runtime_dst, "interop")
        if os.path.isdir(interop_src):
            if os.path.exists(interop_dst):
                shutil.rmtree(interop_dst)
            shutil.copytree(interop_src, interop_dst)
            count = len(os.listdir(interop_dst))
            print(f"  已注入 interop ({count} 个 DLL)")

    # 复制 libmain.so
    libmain_src = os.path.join(REPO, lemon_cfg["libmain"])
    if os.path.isfile(libmain_src):
        libmain_dst = os.path.join(target, "libmain.so")
        shutil.copy2(libmain_src, libmain_dst)
        print(f"  已注入 libmain.so")


def inject_float_window(dest: str, float_cfg: dict, abi: str):
    """注入 ARTEAM 悬浮窗"""
    print(f"\n== 注入 ARTEAM 悬浮窗 ==")
    float_dir = os.path.join(REPO, float_cfg["dex_dir"])
    # abi 由 main 从 config 读取后传入，避免硬编码

    # 1. 加入悬浮窗 dex 作为 classes3.dex（保留官方 classes.dex / classes2.dex）
    dex_files = float_cfg.get("dex_files", ["classes.dex", "classes2.dex"])
    # 配置里的 dex_files 是悬浮窗的，需要重新编号避免与官方 dex 冲突
    # 官方 APK 通常有 classes.dex, classes2.dex；悬浮窗的 dex 放到 classes3.dex
    float_dex_count = len(dex_files)
    # 实际只有一个合并的 classes3.dex（来自 static/float/classes3.dex）
    extra_dex = os.path.join(float_dir, "classes3.dex")
    if os.path.isfile(extra_dex):
        dst = os.path.join(dest, "classes3.dex")
        shutil.copy2(extra_dex, dst)
        print(f"  已加入 classes3.dex (悬浮窗)")
    else:
        # 没有 classes3.dex，合并 classes.dex + classes2.dex 为 classes3.dex
        # 这里简化处理：如果有多个 dex，用 dexconc 合并（需要额外工具）
        # 或者直接复制第一个作为 classes3.dex
        first = os.path.join(float_dir, dex_files[0])
        if os.path.isfile(first):
            shutil.copy2(first, os.path.join(dest, "classes3.dex"))
            print(f"  已加入 classes3.dex (取悬浮窗 {dex_files[0]})")

    # 2. 加入悬浮窗 native 库
    # 优先用配置 native_dir（已含 abi），回退到 float_dir/lib/{abi}
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
    for lib in add_needed:
        # 检查该 lib 是否在目标目录中
        lib_path = os.path.join(dest, "lib", abi, lib)
        if not os.path.isfile(lib_path):
            print(f"  [WARN] {lib} 不在 lib/{abi}/ 中，跳过 DT_NEEDED")
            continue
        try:
            # patchelf --add-needed <lib> <so>
            run(["patchelf", "--add-needed", lib, target])
            print(f"  已添加 DT_NEEDED: {lib}")
        except SystemExit:
            raise
        except Exception as e:
            print(f"  [WARN] patchelf 失败 ({lib}): {e}，跳过")


def patch_manifest_binary(dest: str):
    """尝试给二进制 AndroidManifest.xml 添加 SYSTEM_ALERT_WINDOW 权限
    
    二进制 AXML 修补较复杂，这里采用 apktool 解码 manifest → 修改 → 重编码的方式。
    如果 apktool 不可用或失败，跳过（悬浮窗可能仍可通过其他方式工作）。
    """
    print(f"\n== 修补 AndroidManifest ==")
    manifest_path = os.path.join(dest, "AndroidManifest.xml")
    if not os.path.isfile(manifest_path):
        print(f"  [WARN] 无 AndroidManifest.xml，跳过")
        return

    # 检查是否已是文本格式
    with open(manifest_path, 'rb') as f:
        head = f.read(4)

    if head == b'\x03\x00\x08\x00':
        # 二进制格式，尝试用 apktool
        print("  Manifest 为二进制格式，尝试 apktool 处理...")
        try:
            # 用 apktool 解码 manifest
            r = subprocess.run(
                ["apktool", "d", "-s", "-f", "-o", os.path.join(dest, ".apktool_tmp"), dest],
                capture_output=True, text=True
            )
            if r.returncode != 0:
                print(f"  [WARN] apktool 解码失败，跳过 manifest 修补: {r.stderr[:100]}")
                return

            # 修改解码后的 manifest
            text_manifest = os.path.join(dest, ".apktool_tmp", "AndroidManifest.xml")
            if os.path.isfile(text_manifest):
                txt = open(text_manifest, encoding='utf-8').read()
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
                    open(text_manifest, 'w', encoding='utf-8').write(txt)
                    print(f"  已在文本 manifest 中添加 {PERMISSION}")

                # 重编码回二进制
                r2 = subprocess.run(
                    ["apktool", "b", os.path.join(dest, ".apktool_tmp"), "-o", os.path.join(dest, ".manifest_recoded.apk")],
                    capture_output=True, text=True
                )
                if r2.returncode == 0 and os.path.isfile(os.path.join(dest, ".manifest_recoded.apk")):
                    # 从重编码的 APK 中提取二进制 manifest
                    with zipfile.ZipFile(os.path.join(dest, ".manifest_recoded.apk")) as z:
                        with open(manifest_path, 'wb') as f:
                            f.write(z.read("AndroidManifest.xml"))
                    print(f"  已用 apktool 重编码 manifest")
                    # 清理临时文件
                    shutil.rmtree(os.path.join(dest, ".apktool_tmp"), ignore_errors=True)
                    os.remove(os.path.join(dest, ".manifest_recoded.apk"))
                else:
                    print(f"  [WARN] apktool 重编码失败，保留原 manifest: {r2.stderr[:100]}")
                    shutil.rmtree(os.path.join(dest, ".apktool_tmp"), ignore_errors=True)
            else:
                print("  [WARN] 未找到解码后的 manifest，跳过")
                shutil.rmtree(os.path.join(dest, ".apktool_tmp"), ignore_errors=True)
        except FileNotFoundError:
            print("  [WARN] apktool 不可用，跳过 manifest 修补")
        except Exception as e:
            print(f"  [WARN] manifest 修补异常: {e}，跳过")
    else:
        # 已是文本格式，直接修改
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

    # 5. 修补 manifest
    patch_manifest_binary(unpack_dir)

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
        "components": ["LemonLoader", "ARTEAM悬浮窗", "libunity patched"],
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