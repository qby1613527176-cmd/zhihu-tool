# -*- coding: utf-8 -*-
"""最小但可靠的 wasm code-段指令解码器：逐条走指令，列出 call / call_indirect 及其 typeidx。
用法: python py/wasm_decode.py <build.js> [过滤:call_indirect|call|all]
"""
import io, re, sys, base64, os, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 无立即数的 opcode 区间（wasm 1.0 + 常见扩展）
NOIMM = set()
for a, b in [(0x00, 0x05), (0x0B, 0x0B), (0x0F, 0x0F), (0x1A, 0x1B),
             (0x45, 0xC4), (0xD1, 0xD1)]:
    NOIMM.update(range(a, b + 1))
NOIMM.discard(0x02); NOIMM.discard(0x03); NOIMM.discard(0x04)   # block/loop/if 有 blocktype
NOIMM.discard(0x0C); NOIMM.discard(0x0D)                        # br/br_if 有 LEB


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
            if s < 32 and (x & 0x40):
                r |= -(1 << s)
            return r, i


def extract_wasm(p):
    s = io.open(p, 'r', encoding='utf-8', errors='replace').read()
    for m in re.finditer(r'[A-Za-z0-9+/]{200,}={0,2}', s):
        b64 = m.group()
        try:
            d = base64.b64decode(b64 + '=' * ((4 - len(b64) % 4) % 4))
        except Exception:
            continue
        if d[:4] == b'\x00asm':
            return d
    return None


def sections(d):
    out = {}; i = 8
    while i < len(d):
        idp = i; sid = d[i]; i += 1
        sz, i = uleb(d, i)
        out.setdefault(sid, []).append((idp, i, sz, d[i:i + sz]))
        i += sz
    return out


def decode_body(b, start, end, fidx, calls, inds, bad):
    p = start
    while p < end:
        op = b[p]; p += 1
        if op in NOIMM:
            continue
        if op in (0x02, 0x03, 0x04):            # block/loop/if: blocktype
            if b[p] == 0x40:
                p += 1
            else:
                _, p = sleb(b, p)
            continue
        if op == 0x05:                           # else
            continue
        if op == 0x0B:                           # end
            continue
        if op in (0x0C, 0x0D, 0x10, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0xD2):
            v, p = uleb(b, p)
            if op == 0x10:
                calls.append((fidx, p, v))
            continue
        if op == 0x0E:                           # br_table
            n, p = uleb(b, p)
            for _ in range(n + 1):
                _, p = uleb(b, p)
            continue
        if op == 0x11:                           # call_indirect typeidx tableidx
            t, p = uleb(b, p)
            tb, p = uleb(b, p)
            inds.append((fidx, p, t, tb))
            continue
        if op == 0x1C:                           # select t
            n, p = uleb(b, p)
            p += n
            continue
        if 0x28 <= op <= 0x3E:                   # loads/stores: align + offset
            _, p = uleb(b, p); _, p = uleb(b, p)
            continue
        if op in (0x3F, 0x40):
            p += 1
            continue
        if op == 0x41 or op == 0x42:
            _, p = sleb(b, p)
            continue
        if op == 0x43:
            p += 4; continue
        if op == 0x44:
            p += 8; continue
        if op == 0xD0:
            _, p = sleb(b, p); continue
        if op == 0xFC:
            sub, p = uleb(b, p)
            if sub <= 7:
                _, p = uleb(b, p)
            elif sub == 8:
                _, p = uleb(b, p); p += 1
            elif sub in (9, 13):
                _, p = uleb(b, p)
            elif sub in (10, 11, 12, 14, 15, 16, 17):
                _, p = uleb(b, p); _, p = uleb(b, p)
            continue
        if op == 0xFD:
            sub, p = uleb(b, p)
            p += 16     # 粗略：SIMD 立即数最多 16 字节
            bad.append((fidx, p, 'simd-approx'))
            continue
        bad.append((fidx, p, 'unknown-op 0x%02X' % op))
        return  # 无法继续可靠解码


def main():
    js = sys.argv[1] if len(sys.argv) > 1 else '.probe/oracle/live_clean.js'
    mode = sys.argv[2] if len(sys.argv) > 2 else 'call_indirect'
    d = extract_wasm(js if os.path.isabs(js) else os.path.join(ROOT, js))
    secs = sections(d)
    nimp = 0
    for idp, hdr, sz, body in secs.get(2, []):
        nimp, _ = uleb(body, 0)
    ftypes = []
    for idp, hdr, sz, body in secs.get(3, []):
        p = 0; n, p = uleb(body, p)
        for _ in range(n):
            t, p = uleb(body, p); ftypes.append(t)
    # 名字
    names = {}
    for idp, hdr, sz, body in secs.get(0, []):
        p = 0; l, p = uleb(body, p)
        if body[p:p + l] != b'name':
            continue
        p += l
        while p < len(body):
            sub = body[p]; p += 1
            ssz, p = uleb(body, p); end = p + ssz
            if sub == 1:
                n, q = uleb(body, p)
                for _ in range(n):
                    idx, q = uleb(body, q); ln, q = uleb(body, q)
                    names[idx] = body[q:q + ln].decode('utf-8', 'replace'); q += ln
            p = end

    calls = []; inds = []; bad = []
    for idp, hdr, sz, body in secs.get(10, []):
        n, p = uleb(body, 0)
        for k in range(n):
            bsz, p = uleb(body, p)
            decode_body(body, p, p + bsz, k, calls, inds, bad)
            p += bsz
    print('定义函数 %d，导入 %d，call 站点 %d，call_indirect 站点 %d，解码中断 %d'
          % (len(ftypes), nimp, len(calls), len(inds), len(bad)))
    if mode in ('call_indirect', 'all'):
        bytype = collections.Counter(t for _, _, t, _ in inds)
        print('call_indirect 按 typeidx 分布:', dict(sorted(bytype.items())))
        for fidx, off, t, tb in inds:
            g = fidx + nimp
            print('  func%-4d %-46s typeidx=%-3d table=%d' % (g, names.get(g, '?')[:46], t, tb))
    if mode == 'all':
        for fidx, off, v in calls[:40]:
            g = fidx + nimp
            print('  call  func%-4d -> func%-4d %s' % (g, v, names.get(v, '?')[:40]))
    if bad:
        print('解码中断样本:', bad[:5])


if __name__ == '__main__':
    main()
