#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_check_compat.py — 汉化插件兼容性静态检查

原理：
  IL2CPP 的 global-metadata.dat 里，类型名/方法名等以 UTF-8 字符串常量形式保存；
  libil2cpp.so 的字符串池里也保留大量类型名。因此可以在不解包 IL2CPP、不跑
  Cpp2IL 的前提下，用"字符串存在性"做一次成本极低的兼容性预检。

  这一步不能证明 Harmony patch 会成功（签名变化、泛型、内联仍可能失败），
  但能可靠地挡住最坏情况：类被删除/重命名 → 直接拒绝出包。

用法:
    python3 scripts/03_check_compat.py --apk <apk>            # 直接从 apk 里读
    python3 scripts/03_check_compat.py --dir <解包目录>        # 从已解包目录读
    可选: --json <输出路径>  --baseline <上次的 json>  (做差异对比)

退出码: 0 = 通过（critical 全部命中且命中率达标）；1 = 拒绝出包
"""
import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO, "config", "mod.json")

META_CANDIDATES = [
    "assets/bin/Data/Managed/Metadata/global-metadata.dat",
    "assets/bin/Data/Managed/Metadata/global-metadata.dat",
]


def find_metadata(root):
    """在解包目录里找 global-metadata.dat"""
    for rel in META_CANDIDATES:
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return p
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn == "global-metadata.dat":
                return os.path.join(dirpath, fn)
    return None


def find_il2cpp(root):
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn == "libil2cpp.so":
                return os.path.join(dirpath, fn)
    return None


def read_blob(path):
    if not path or not os.path.isfile(path):
        return b""
    with open(path, "rb") as f:
        return f.read()


def has_token(blob, name):
    """在二进制里找独立的 UTF-8 标识符（前后不是标识符字符），避免子串误命中"""
    if not name:
        return False
    pat = re.compile(rb"(?<![A-Za-z0-9_])" + re.escape(name.encode("utf-8")) + rb"(?![A-Za-z0-9_])")
    return pat.search(blob) is not None


def count_occurrences(blob, name):
    if not name:
        return 0
    pat = re.compile(rb"(?<![A-Za-z0-9_])" + re.escape(name.encode("utf-8")) + rb"(?![A-Za-z0-9_])")
    return len(pat.findall(blob))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk")
    ap.add_argument("--dir")
    ap.add_argument("--json", default=os.path.join(REPO, "work", "compat_report.json"))
    ap.add_argument("--baseline")
    args = ap.parse_args()

    if not args.apk and not args.dir:
        ap.error("必须给 --apk 或 --dir")

    tmp = None
    root = args.dir
    if args.apk:
        import tempfile
        import zipfile
        tmp = tempfile.mkdtemp(prefix="tsk_compat_")
        root = tmp
        wanted = ("global-metadata.dat", "libil2cpp.so")
        try:
            with zipfile.ZipFile(args.apk) as z:
                for info in z.infolist():
                    if os.path.basename(info.filename) in wanted:
                        dest = os.path.join(tmp, info.filename)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with z.open(info) as src, open(dest, "wb") as dst:
                            while True:
                                b = src.read(1 << 20)
                                if not b:
                                    break
                                dst.write(b)
        except Exception as e:
            print(f"[FAIL] 无法从 apk 提取 IL2CPP 文件: {e}")
            return 1

    cfg = json.load(open(CONFIG, encoding="utf-8"))
    compat = cfg["compat"]
    req_types = compat["required_types"]
    req_members = compat.get("required_members", [])
    min_ratio = float(compat.get("min_type_hit_ratio", 0.85))

    meta_path = find_metadata(root)
    il2cpp_path = find_il2cpp(root)
    print("== 03_check_compat ==")
    print(f"global-metadata.dat : {meta_path or '未找到'}")
    print(f"libil2cpp.so        : {il2cpp_path or '未找到'}")

    meta = read_blob(meta_path)
    il2cpp = read_blob(il2cpp_path)
    if not meta:
        print("[FAIL] 缺少 global-metadata.dat，无法判定兼容性")
        return 1
    print(f"metadata 大小: {len(meta):,}  libil2cpp 大小: {len(il2cpp):,}")
    print()

    results = []
    crit_fail = []
    for t in req_types:
        name = t["name"]
        ns = t.get("namespace", "") or ""
        hit_meta = has_token(meta, name)
        hit_so = has_token(il2cpp, name)
        full = f"{ns}.{name}" if ns else name
        hit_full = has_token(meta, full) if ns else hit_meta
        ok = hit_meta or hit_so
        status = "HIT" if ok else "MISS"
        if not ok and t.get("critical"):
            crit_fail.append(full)
        results.append({
            "type": full,
            "critical": bool(t.get("critical")),
            "hit": ok,
            "in_metadata": hit_meta,
            "in_il2cpp": hit_so,
            "full_name_present": hit_full,
        })
        flag = "!" if t.get("critical") else " "
        print(f" [{status:4}]{flag} {full:45} meta={int(hit_meta)} so={int(hit_so)}")

    hits = sum(1 for r in results if r["hit"])
    ratio = hits / len(results) if results else 0.0
    print(f"\n类型命中: {hits}/{len(results)} = {ratio:.1%}  (阈值 {min_ratio:.0%})")

    member_results = []
    if req_members:
        print("\n-- 关键方法 --")
        for m in req_members:
            name = m["member"]
            hit = has_token(meta, name) or has_token(il2cpp, name)
            member_results.append({"type": m["type"], "member": name, "critical": bool(m.get("critical")), "hit": hit})
            print(f" [{'HIT' if hit else 'MISS':4}] {m['type']}.{name}")

    # 与上次报告对比，看有没有新增缺失
    diff = {}
    if args.baseline and os.path.isfile(args.baseline):
        try:
            old = json.load(open(args.baseline, encoding="utf-8"))
            old_hit = {r["type"] for r in old.get("types", []) if r.get("hit")}
            new_hit = {r["type"] for r in results if r.get("hit")}
            diff = {"lost": sorted(old_hit - new_hit), "gained": sorted(new_hit - old_hit)}
            if diff["lost"]:
                print(f"\n[回归] 相比基线丢失的类型: {diff['lost']}")
        except Exception as e:
            print(f"(基线对比跳过: {e})")

    ok = (not crit_fail) and ratio >= min_ratio
    report = {
        "apk_or_dir": args.apk or args.dir,
        "metadata_size": len(meta),
        "il2cpp_size": len(il2cpp),
        "types": results,
        "members": member_results,
        "hit_ratio": ratio,
        "min_type_hit_ratio": min_ratio,
        "critical_missing": crit_fail,
        "diff_vs_baseline": diff,
        "ok": ok,
    }
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告: {args.json}")

    if crit_fail:
        print(f"\n== 拒绝出包 ❌ 关键类型缺失: {', '.join(crit_fail)} ==")
        return 1
    if ratio < min_ratio:
        print(f"\n== 拒绝出包 ❌ 命中率 {ratio:.1%} 低于阈值 ==")
        return 1
    print("\n== 兼容性预检通过 ✅（仍需真机验证 Harmony patch 实际生效） ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())