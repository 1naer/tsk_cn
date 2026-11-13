#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dex_inspect.py — 快速列出 dex 内所有类描述符，统计包前缀，并可与另一 dex 求交集。

用法:
  python3 dex_inspect.py <a.dex> [b.dex] [--top N] [--grep 前缀]
"""
import struct
import sys
import collections


def read_uleb(data, off):
    result = 0
    shift = 0
    while True:
        b = data[off]
        off += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, off


def parse_dex(path):
    data = open(path, "rb").read()
    if data[:4] not in (b"dex\n",):
        raise ValueError(f"{path} 不是 dex")
    string_ids_size, string_ids_off = struct.unpack("<II", data[56:64])
    type_ids_size, type_ids_off = struct.unpack("<II", data[64:72])
    class_defs_size, class_defs_off = struct.unpack("<II", data[96:104])

    def get_string(idx):
        off = struct.unpack("<I", data[string_ids_off + idx * 4:string_ids_off + idx * 4 + 4])[0]
        _, p = read_uleb(data, off)
        end = data.index(b"\x00", p)
        return data[p:end].decode("utf-8", "replace")

    type_names = []
    for i in range(type_ids_size):
        sidx = struct.unpack("<I", data[type_ids_off + i * 4:type_ids_off + i * 4 + 4])[0]
        type_names.append(get_string(sidx))

    classes = []
    for i in range(class_defs_size):
        base = class_defs_off + i * 32
        tidx = struct.unpack("<I", data[base:base + 4])[0]
        classes.append(type_names[tidx])
    return classes


def to_java(desc):
    return desc.lstrip("L").rstrip(";")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    top = 20
    grep = None
    for a in sys.argv[1:]:
        if a.startswith("--top"):
            top = int(a.split("=")[1]) if "=" in a else top
        if a.startswith("--grep="):
            grep = a.split("=", 1)[1]

    if not args:
        print(__doc__)
        return 2

    all_sets = []
    for p in args:
        cs = parse_dex(p)
        all_sets.append(set(cs))
        print(f"== {p} ==")
        print(f"类总数: {len(cs)}")
        pk = collections.Counter()
        for c in cs:
            j = to_java(c)
            parts = j.split("/")
            pref = "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
            pk[pref] += 1
        print("包前缀 Top:")
        for k, v in pk.most_common(top):
            print(f"  {v:6d}  {k}")
        if grep:
            hit = [to_java(c) for c in cs if grep in c]
            print(f"包含 '{grep}' 的类: {len(hit)}")
            for h in hit[:15]:
                print("   ", h)
        print()

    if len(all_sets) >= 2:
        a = {to_java(c) for c in all_sets[0]}
        b = {to_java(c) for c in all_sets[1]}
        inter = a & b
        print(f"== 交集 {args[0]} ∩ {args[1]} ==")
        print(f"共同类: {len(inter)}")
        pk = collections.Counter("/".join(c.split("/")[:2]) for c in inter)
        for k, v in pk.most_common(15):
            print(f"  {v:6d}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())