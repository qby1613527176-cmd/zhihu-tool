# -*- coding: utf-8 -*-
"""在 raw .wasm 的任意字节偏移插 fd_write 断点（自动定位所属函数体并修 size LEB）。

用法: python py/wasm_patch_raw.py <in.wasm> <out.wasm> <hex_offset> [local1,local2,local3]

插桩内容: local.get L1; local.get L2; local.get L3; i32.const 0x5A5A5A5A; call 0; drop
⇒ JS 侧 fd_write 收到 (fd,iovs,cnt,nwritten) = (L1,L2,L3,魔数)。

`wasm_patch_at.py`（输入是内嵌 wasm 的 build JS）复用本文件的 `patch()`。
"""
import io, os, sys


def uleb(b, i):
    r = 0; s = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 0x7f) << s; s += 7
        if not x & 0x80:
            return r, i


def leb(n):
    o = bytearray()
    while True:
        x = n & 0x7f; n >>= 7
        if n:
            o.append(x | 0x80)
        else:
            o.append(x); break
    return bytes(o)


def patch(d, off, locs, wrap=False, quiet=False):
    """在 `d`（wasm 字节）的 `off` 处插入断点调用，返回新字节。"""
    assert d[:4] == b'\x00asm', 'not a wasm file'
    locs = list(locs)[:3]
    while len(locs) < 3:
        locs.append(locs[-1])
    i = 8; secs = []
    while i < len(d):
        idp = i; sid = d[i]; i += 1
        sz, i = uleb(d, i)
        secs.append({'id': sid, 'idp': idp, 'hdr': i, 'size': sz})
        i += sz
    code = [s for s in secs if s['id'] == 10][0]
    body = d[code['hdr']:code['hdr'] + code['size']]
    p = 0; n, p = uleb(body, p)
    off_in_sec = off - code['hdr']
    found = None
    for k in range(n):
        bsz_off = p
        bsz, p = uleb(body, p)
        bstart = p
        if bstart <= off_in_sec < bstart + bsz:
            found = (k, bsz_off, bsz, bstart, off_in_sec - bstart); break
        p += bsz
    if not found:
        if not quiet:
            print('偏移 0x%X 不在任何 body 内' % off)
        return None
    k, bsz_off, bsz, bstart, rel = found
    if not quiet:
        print('目标 0x%X ⇒ body#%d（相对 +%d，body 大小 %d）' % (off, k, rel, bsz))

    pro = bytearray()
    for L in locs:
        pro.append(0x20); pro.append(L)
        if wrap:
            pro.append(0xA7)
    pro += bytes([0x41, 0xDA, 0xB4, 0xE9, 0xD2, 0x05])   # i32.const 0x5A5A5A5A
    pro += bytes([0x10, 0x00, 0x1A])                     # call 0; drop
    # 插入点若在 locals 之前，后移到指令起点
    _q = bstart
    _ng, _q = uleb(body, _q)
    for _ in range(_ng):
        _c, _q = uleb(body, _q); _q += 1
    if bstart + rel <= _q:
        rel = _q - bstart
        if not quiet:
            print('  插入点在 locals 之前 ⇒ 后移到 rel=%d' % rel)

    PRO = bytes(pro)
    nb = body[:bstart + rel] + PRO + body[bstart + rel:]
    nb = nb[:bsz_off] + leb(bsz + len(PRO)) + nb[bsz_off + len(leb(bsz)):]
    return d[:code['idp']] + bytes([10]) + leb(len(nb)) + nb + d[code['hdr'] + code['size']:]


def main():
    src = sys.argv[1]
    out = sys.argv[2]
    off = int(sys.argv[3], 16)
    locs = [int(x) for x in (sys.argv[4].split(',') if len(sys.argv) > 4 else ['1'])]
    wrap = os.environ.get('WRAP64') == '1'

    d = io.open(src, 'rb').read()
    patched = patch(d, off, locs, wrap)
    if patched is None:
        return 1
    io.open(out, 'wb').write(patched)
    print('文件 %d -> %d 字节；插在 0x%X（locals %s）' % (len(d), len(patched), off, locs))
    return 0


if __name__ == '__main__':
    sys.exit(main())
