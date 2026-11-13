#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dex_diff.py — 计算两个 dex 的差集，并按包前缀统计，用于提取"真正增量"

用法:
  python3 dex_diff.py <base.dex> <mod.dex> [--dump=前缀] [--limit N]
"""
import collections
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dex_inspect import parse_dex, to_java


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dump = None
    limit = 60
    for a in sys.argv[1:]:
        if a.startswith("--dump="):
            dump = a.split("=", 1)[1]
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])
    if len(args) < 2:
        print(__doc__)
        return 2

    base = {to_java(c) for c in parse_dex(args[0])}
    mod = {to_java(c) for c in parse_dex(args[1])}
    only_mod = sorted(mod - base)
    missing = sorted(base - mod)
    print(f"base({os.path.basename(args[0])}) = {len(base)}")
    print(f"mod ({os.path.basename(args[1])}) = {len(mod)}")
    print(f"mod 独有(增量) = {len(only_mod)}")
    print(f"mod 缺失(被删/旧版) = {len(missing)}")
    print()
    print("-- 增量包前缀 --")
    pk = collections.Counter("/".join(c.split("/")[:2]) if "/" in c else c for c in only_mod)
    for k, v in pk.most_common(30):
        print(f"  {v:5d}  {k}")
    print()
    if dump:
        sel = [c for c in only_mod if c.startswith(dump)]
        print(f"-- 增量中 '{dump}' 开头共 {len(sel)} 个，前 {limit} --")
        for c in sel[:limit]:
            print("   ", c)
    return 0


if __name__ == "__main__":
    sys.exit(main())