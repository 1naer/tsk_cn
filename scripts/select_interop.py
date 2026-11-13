#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""select_interop.py — 从 Cpp2IL 生成结果中，按旧 interop 清单筛选需要更新的程序集。

安全策略（关键）：
    以旧 interop 目录的 *.dll 文件名清单为准，只从 Cpp2IL 新生成结果里取**同名**文件。
    这样：
      1. 只更新已存在的 interop 程序集，保持目录结构一致；
      2. 不会把游戏本体 DLL（如 Assembly-CSharp.dll 等）误当作 interop 塞进 LemonLoader；
      3. 缺失的清单项会明确报告，方便人工判断是否需要补全。

用法：
    select_interop.py <new_gen_dir> <old_interop_dir> <out_dir>
"""
import os
import sys
import shutil


def main():
    if len(sys.argv) < 4:
        print("用法: select_interop.py <new_gen_dir> <old_interop_dir> <out_dir>")
        return 2

    new_dir, old_dir, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]

    if not os.path.isdir(new_dir):
        print(f"[FAIL] 生成目录不存在: {new_dir}")
        return 1
    if not os.path.isdir(old_dir):
        print(f"[FAIL] 旧 interop 目录不存在: {old_dir}")
        return 1

    os.makedirs(out_dir, exist_ok=True)

    # 旧 interop 清单（作为需要更新的“白名单”）
    old_names = {fn for fn in os.listdir(old_dir) if fn.lower().endswith('.dll')}
    print(f"旧 interop 清单: {len(old_names)} 个 dll")

    # 索引新生成结果中的所有 dll
    new_index = {}
    for root, _, files in os.walk(new_dir):
        for fn in files:
            if fn.lower().endswith('.dll'):
                new_index[fn] = os.path.join(root, fn)
    print(f"Cpp2IL 生成结果: {len(new_index)} 个 dll")

    hit, miss = 0, []
    for name in sorted(old_names):
        if name in new_index:
            shutil.copy2(new_index[name], os.path.join(out_dir, name))
            hit += 1
        else:
            miss.append(name)

    print(f"命中: {hit}/{len(old_names)}")
    if miss:
        preview = miss[:20]
        more = "..." if len(miss) > 20 else ""
        print(f"缺失({len(miss)}): {preview}{more}")
    print(f"已输出到: {out_dir}")
    return 0


if __name__ == '__main__':
    sys.exit(main())