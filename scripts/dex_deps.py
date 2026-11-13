#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dex_deps.py — 检查一个 dex 里所有类的"外部引用"，用于判断该 dex 能否作为
独立增量 dex 插入（引用的类是否都来自 Android 框架或本 dex 自身）。

用法:
  python3 dex_deps.py <mod.dex> [--base <base.dex> ...] [--show 前缀]
"""
import struct
import sys
import os
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dex_inspect import parse_dex, to_java, read_uleb


def all_type_refs(path):
    data = open(path, "rb").read()
    string_ids_size, string_ids_off = struct.unpack("<II", data[56:64])
    type_ids_size, type_ids_off = struct.unpack("<II", data[64:72])

    def get_string(idx):
        off = struct.unpack("<I", data[string_ids_off + idx * 4:string_ids_off + idx * 4 + 4])[0]
        _, p = read_uleb(data, off)
        end = data.index(b"\x00", p)
        return data[p:end].decode("utf-8", "replace")

    types = []
    for i in range(type_ids_size):
        sidx = struct.unpack("<I", data[type_ids_off + i * 4:type_ids_off + i * 4 + 4])[0]
        types.append(get_string(sidx))
    return types


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    bases = []
    show = None
    i = 0
    while i < len(sys.argv[1:]):
        a = sys.argv[1:][i]
        if a == "--base" and i + 1 < len(sys.argv[1:]):
            bases.append(sys.argv[1:][i + 1])
        if a.startswith("--show="):
            show = a.split("=", 1)[1]
        i += 1

    if not args:
        print(__doc__)
        return 2
    mod_path = args[0]

    mod_classes = {to_java(c) for c in parse_dex(mod_path)}
    refs = set()
    for t in all_type_refs(mod_path):
        j = to_java(t)
        if not j or j.startswith("["):
            j = j.lstrip("[")
            j = j.lstrip("L").rstrip(";")
        if j:
            refs.add(j)

    base_types = set()
    for b in bases:
        base_types |= {to_java(t) for t in all_type_refs(b)}

    external = sorted(refs - mod_classes)
    android_fw = [r for r in external if r.startswith(("java/", "javax/", "android/", "dalvik/", "org/xml/", "org/json/", "org/apache/"))]
    other = [r for r in external if r not in set(android_fw)]

    print(f"== {os.path.basename(mod_path)} ==")
    print(f"自身类: {len(mod_classes)}")
    print(f"类型引用总数: {len(refs)}")
    print(f"外部引用: {len(external)}  (Android/Java 框架 {len(android_fw)}, 其他 {len(other)})")
    print()
    print("-- 其他外部引用按前缀 --")
    pk = collections.Counter("/".join(c.split("/")[:2]) if "/" in c else c for c in other)
    for k, v in pk.most_common(25):
        tag = ""
        if base_types:
            n_in_base = sum(1 for c in other if c.startswith(k + "/") and c in base_types)
            tag = f"  (其中在 base 中: {n_in_base})"
        print(f"  {v:5d}  {k}{tag}")
    print()
    if show:
        sel = [c for c in other if c.startswith(show)]
        print(f"-- '{show}' 开头共 {len(sel)} --")
        for c in sel:
            inb = " [base有]" if c in base_types else " [base无!]"
            print(f"   {c}{inb if base_types else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())