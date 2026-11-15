#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
axml_patch.py (v2) — 二进制 AndroidManifest.xml 权限注入器

v1 的三处结构性 bug（导致 apksig 报 malformed binary resource）：
  1. 新增 UTF-16 字符串缺少 NUL 终止符
     -> "UTF-16 encoded form of string not NULL terminated"
  2. 新 offset 条目被追加到字符串池末尾，而不是插进 offset 数组
     -> offsets 数组与字符串数据区错位
  3. 手工构造的 END_ELEMENT 只有 16 字节，规范要求 24 字节（含 ns + name）

v2 策略（保守、可自校验）：
  * 原字符串数据区字节原样保留，只在尾部追加新字符串（len u16 + UTF-16 + \\x00\\x00）
  * 重建字符串池 = 新 header + 新 offset 数组(count+1) + 原数据区 + 新串 + 4 字节对齐
  * 元素不手搓：克隆一个现成的 <uses-permission> 节点（56B START + 24B END），
    只把属性里的 rawValue 与 data 换成新权限字符串索引
  * XML 树其余 chunk 的 size 字段完全不变，只重写根 chunk 的 size
  * 修补后立即重新解析自校验（权限串 + 权限节点同时命中才算成功）

用法:
    python3 scripts/axml_patch.py <AndroidManifest.xml> <permission>
    python3 scripts/axml_patch.py --check <AndroidManifest.xml>
退出码:
    0 = 成功 / 已存在（无需改动）
    1 = 失败
"""
import struct
import sys

RES_XML_TYPE = 0x0003
RES_STRING_POOL_TYPE = 0x0001
RES_XML_START_ELEMENT_TYPE = 0x0102
RES_XML_END_ELEMENT_TYPE = 0x0103

UTF8_FLAG = 1 << 8

ELEM_HEADER_SIZE = 16          # type/headerSize/size/lineNumber/comment
START_ELEM_EXT = 20            # ns/name/attrStart/attrSize/attrCount/id/class/style
ATTR_SIZE = 20


class AxmlError(Exception):
    pass


def _u16(d, o):
    return struct.unpack_from('<H', d, o)[0]


def _u32(d, o):
    return struct.unpack_from('<I', d, o)[0]


class StringPool:
    """字符串池：只读原有字符串，写入时按规范重建"""

    def __init__(self, data, off):
        if _u16(data, off) != RES_STRING_POOL_TYPE:
            raise AxmlError('offset %d 不是字符串池' % off)
        self.off = off
        self.header_size = _u16(data, off + 2)
        self.size = _u32(data, off + 4)
        self.count = _u32(data, off + 8)
        self.style_count = _u32(data, off + 12)
        self.flags = _u32(data, off + 16)
        self.strings_start = _u32(data, off + 20)

        if self.flags & UTF8_FLAG:
            raise AxmlError('暂不支持 UTF-8 字符串池 (flags=%#x)' % self.flags)
        if self.style_count != 0:
            raise AxmlError('暂不支持带 style 的字符串池 (styleCount=%d)' % self.style_count)

        self.offsets = list(struct.unpack_from('<%dI' % self.count,
                                               data, off + self.header_size))
        self.base = off + self.strings_start
        # 数据区原样保留（可能含尾部 padding，不影响正确性）
        self.data_blob = bytes(data[self.base:off + self.size])
        self.strings = [self._read(data, i) for i in range(self.count)]

    def _read(self, data, i):
        o = self.base + self.offsets[i]
        ln = _u16(data, o)
        return data[o + 2:o + 2 + ln * 2].decode('utf-16-le', 'replace')

    def index_of(self, s):
        for i, v in enumerate(self.strings):
            if v == s:
                return i
        return -1

    def append(self, s):
        """返回 (新字符串索引, 重建后的字符串池字节)"""
        if self.index_of(s) >= 0:
            return self.index_of(s), None

        new_index = self.count
        raw = s.encode('utf-16-le')
        # len(u16) + UTF-16 数据 + NUL 终止符（关键修复点 1）
        enc = struct.pack('<H', len(s)) + raw + b'\x00\x00'
        if len(enc) % 4:
            enc += b'\x00' * (4 - len(enc) % 4)

        new_offsets = self.offsets + [len(self.data_blob)]
        strings_start = self.header_size + 4 * len(new_offsets)
        blob = self.data_blob + enc
        total = strings_start + len(blob)
        pad = (4 - total % 4) % 4

        header = struct.pack('<HHIIIIII',
                             RES_STRING_POOL_TYPE, self.header_size,
                             total + pad, len(new_offsets), 0, 0,
                             strings_start, 0)
        pool = header
        pool += struct.pack('<%dI' % len(new_offsets), *new_offsets)
        pool += blob
        pool += b'\x00' * pad
        return new_index, pool


def iter_chunks(data, start, end):
    pos = start
    while pos + 8 <= end:
        ctype = _u16(data, pos)
        hsz = _u16(data, pos + 2)
        size = _u32(data, pos + 4)
        if size < 8 or pos + size > end:
            raise AxmlError('chunk 在 %d 处长度非法 (%d)' % (pos, size))
        yield pos, ctype, hsz, size
        pos += size


def find_uses_permission(data, tree_start, tree_end, name_index):
    """返回第一个 depth==1 的 <uses-permission> 元素 (start, end_exclusive)"""
    depth = 0
    for pos, ctype, hsz, size in iter_chunks(data, tree_start, tree_end):
        if ctype == RES_XML_START_ELEMENT_TYPE:
            # 注意: AXML 节点 chunk 的 headerSize 恒为 16（ResXMLTree_node），
            # 20 字节的 START_ELEMENT ext 不计入 headerSize，因此要校验 chunk size
            if hsz < ELEM_HEADER_SIZE or size < ELEM_HEADER_SIZE + START_ELEM_EXT:
                raise AxmlError('START_ELEMENT 长度不足 (hsz=%d size=%d)' % (hsz, size))
            nm = _u32(data, pos + 20)
            if depth == 1 and nm == name_index:
                endpos = pos + size
                if _u16(data, endpos) != RES_XML_END_ELEMENT_TYPE:
                    raise AxmlError('uses-permission 后不是 END_ELEMENT')
                esz = _u32(data, endpos + 4)
                if _u32(data, endpos + 20) != nm:
                    raise AxmlError('END_ELEMENT name 不匹配')
                return pos, endpos + esz
            depth += 1
        elif ctype == RES_XML_END_ELEMENT_TYPE:
            depth -= 1
    raise AxmlError('未找到 <uses-permission> 元素')


def element_has_permission(data, start, size, perm_index):
    """检查某 START_ELEMENT 的属性里是否已引用给定权限字符串"""
    hsz = _u16(data, start + 2)
    attr_start = _u16(data, start + 24)
    attr_count = _u16(data, start + 28)
    a = start + hsz + attr_start
    for _ in range(attr_count):
        if _u32(data, a + 16) == perm_index:   # typedValue.data（字符串引用）
            return True
        a += ATTR_SIZE
    return False


def permission_present(data, permission):
    """返回 (字符串索引或-1, 是否已有引用该权限的元素)"""
    pool = StringPool(data, 8)
    idx = pool.index_of(permission)
    if idx < 0:
        return -1, False
    rm_off = 8 + pool.size
    rm_size = _u32(data, rm_off + 4)
    tree_start = rm_off + rm_size
    tree_end = len(data)
    name_idx = pool.index_of('uses-permission')
    if name_idx < 0:
        return idx, False
    for pos, ctype, hsz, size in iter_chunks(data, tree_start, tree_end):
        if ctype == RES_XML_START_ELEMENT_TYPE and _u32(data, pos + 20) == name_idx:
            if element_has_permission(data, pos, size, idx):
                return idx, True
    return idx, False


def build_element(template, perm_index):
    """克隆模板元素（56B START + 24B END），替换权限字符串索引"""
    blk = bytearray(template)
    hsz = _u16(blk, 2)
    attr_start = _u16(blk, 24)
    a = hsz + attr_start
    # rawValue 与 typedValue.data 都指向权限字符串
    struct.pack_into('<I', blk, a + 8, perm_index)
    struct.pack_into('<I', blk, a + 16, perm_index)
    return bytes(blk)


def patch_manifest(data, permission):
    if len(data) < 8 or _u16(data, 0) != RES_XML_TYPE:
        print('[WARN] 非二进制 AXML（magic=%s），跳过' % data[:4].hex())
        return None

    pool = StringPool(data, 8)
    perm_index = pool.index_of(permission)
    rm_off = 8 + pool.size
    rm_size = _u32(data, rm_off + 4)
    tree_start = rm_off + rm_size
    tree_end = len(data)

    name_idx = pool.index_of('uses-permission')
    if name_idx < 0:
        print('[FAIL] 字符串池缺少 uses-permission')
        return None

    # 已有元素引用该权限 -> 无需改动
    if perm_index >= 0:
        try:
            for pos, ctype, hsz, size in iter_chunks(data, tree_start, tree_end):
                if ctype == RES_XML_START_ELEMENT_TYPE and _u32(data, pos + 20) == name_idx:
                    if element_has_permission(data, pos, size, perm_index):
                        print('[SKIP] 权限 %s 已存在（字符串+节点）' % permission)
                        return data
        except AxmlError:
            pass

    # 1. 追加权限字符串
    if perm_index < 0:
        perm_index, new_pool = pool.append(permission)
        print('[OK] 已追加字符串 %r -> index %d' % (permission, perm_index))
    else:
        new_pool = None
        print('[OK] 字符串 %r 已存在 -> index %d，仅补节点' % (permission, perm_index))

    if new_pool is None:
        new_pool = bytes(data[8:8 + pool.size])

    # 2. 克隆现成的 uses-permission 元素
    s, e = find_uses_permission(data, tree_start, tree_end, name_idx)
    template = data[s:e]
    elem = build_element(template, perm_index)

    # 3. 插到该元素 END 之后（仍位于 manifest 的子节点区间）
    tree = data[tree_start:e] + elem + data[e:tree_end]

    new_root_size = 8 + len(new_pool) + rm_size + len(tree)
    out = bytearray()
    out += struct.pack('<HHI', RES_XML_TYPE, 8, new_root_size)
    out += new_pool
    out += data[rm_off:rm_off + rm_size]
    out += tree
    return bytes(out)


def self_check(data, permission):
    try:
        idx, referenced = permission_present(data, permission)
    except AxmlError as exc:
        print('[FAIL] 自校验解析失败: %s' % exc)
        return False
    if idx < 0 or not referenced:
        print('[FAIL] 自校验未通过: index=%d referenced=%s' % (idx, referenced))
        return False
    print('[OK] 自校验通过: %s -> index %d，节点引用正常' % (permission, idx))
    return True


def main():
    argv = sys.argv[1:]
    check_only = '--check' in argv
    args = [a for a in argv if not a.startswith('--')]

    if check_only:
        if len(args) != 1:
            print('用法: axml_patch.py --check <manifest.xml>')
            return 1
        data = open(args[0], 'rb').read()
        pool = StringPool(data, 8)
        print('[INFO] 字符串数=%d 池大小=%d 文件大小=%d'
              % (pool.count, pool.size, len(data)))
        return 0

    if len(args) != 2:
        print('用法: axml_patch.py <manifest.xml> <permission>')
        return 1

    path, permission = args
    data = open(path, 'rb').read()
    try:
        new = patch_manifest(data, permission)
    except AxmlError as exc:
        print('[FAIL] %s' % exc)
        return 1

    if new is None:
        return 1
    if new == data:
        return 0

    if not self_check(new, permission):
        return 1

    with open(path, 'wb') as f:
        f.write(new)
    print('[OK] 已写入 %s（%d -> %d 字节）' % (path, len(data), len(new)))
    return 0


if __name__ == '__main__':
    sys.exit(main())