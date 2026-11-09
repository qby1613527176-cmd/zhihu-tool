# -*- coding: utf-8 -*-
"""从指定 wasm 的 data 段生成 .probe/zsec/_datasegs.json（[[memaddr, hex], ...]）。"""
import io, json, sys


def uleb(b, i):
    r = 0; s = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 0x7f) << s; s += 7
        if not x & 0x80:
            return r, i


def sleb(b, i):
    r = 0; s = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 0x7f) << s; s += 7
        if not x & 0x80:
            if x & 0x40:
                r -= (1 << s)
            return r, i


def main():
    src = sys.argv[1]
    out = sys.argv[2]
    d = io.open(src, 'rb').read()
    p = 8
    segs = None
    while p < len(d):
        sid = d[p]; p += 1
        sz, p0 = uleb(d, p)
        if sid == 11:
            segs = d[p0:p0 + sz]
        p = p0 + sz
    assert segs is not None, 'no data section'
    q = 0
    n, q = uleb(segs, q)
    out_list = []
    for k in range(n):
        flags, q = uleb(segs, q)
        addr = None
        if flags == 0:
            assert segs[q] == 0x41
            v, q = sleb(segs, q + 1)
            assert segs[q] == 0x0b
            q += 1
            addr = v
        elif flags == 2:
            _, q = uleb(segs, q)
            assert segs[q] == 0x41
            v, q = sleb(segs, q + 1)
            assert segs[q] == 0x0b
            q += 1
            addr = v
        size, q = uleb(segs, q)
        data = segs[q:q + size]
        q += size
        if addr is not None and size:
            out_list.append([addr, data.hex()])
    io.open(out, 'w', encoding='utf-8').write(json.dumps(out_list))
    print('%s -> %s : %d 段, 共 %d 字节' % (src, out, len(out_list), sum(len(x[1]) // 2 for x in out_list)))
    for a, h in out_list:
        print('   @%d len=%d' % (a, len(h) // 2))


main()
