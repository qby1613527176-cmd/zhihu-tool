# -*- coding: utf-8 -*-
"""解码 zh 的 A/B/D 段（链式 XOR）。编码：out_i = (c_i ^ k_i) ^ out_{i-1}，out_{-1}=acc0
⇒ c_i = out_i ^ k_i ^ out_{i-1} (i>=1)；c_0 由 acc0 定。用「明文应可打印」定 acc0。"""
import io

KEYS = ['b53cc3fa93dc8013', '6a6cb1bb3970bae6', '44ae0b7a68bf0f7a', 'acc7471d3eb9bc30', 'f25b8434cca9a1f5']

def dec(enc, key):
    out = [ord(c) for c in enc]
    k = [ord(c) for c in key]
    c = [None] * len(out)
    for i in range(1, len(out)):
        c[i] = out[i] ^ k[i % len(k)] ^ out[i - 1]
    res = []
    for a in range(256):
        c0 = out[0] ^ k[0] ^ a
        s = chr(c0) + ''.join(chr(x) for x in c[1:])
        printable = sum(1 for ch in s if 32 <= ord(ch) < 127)
        res.append((printable, a, s))
    res.sort(reverse=True)
    return res[:3]

z = io.open('.probe/zsec/zh_mine_raw.txt', encoding='utf-8').read()
segs = z.split(chr(0x200C))
print('我的 zh 长度 %d  分段 %s' % (len(z), [len(s) for s in segs]))
for idx, name in ((0, 'A(13)'), (1, 'B(16)'), (3, 'D(8)')):
    if idx >= len(segs): continue
    s = segs[idx]
    print('--- 段%s 原始 %r' % (name, s))
    for key in KEYS:
        best = dec(s, key)[0]
        print('    key=%s → 可打印 %d/%d  acc0=%d  明文=%r' % (key, best[0], len(s), best[1], best[2]))
print()
print('参考：真浏览器 seg0 = %r（13 字符）' % '--{z+-}{)y""t')
