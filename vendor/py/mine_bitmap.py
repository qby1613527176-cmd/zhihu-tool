# -*- coding: utf-8 -*-
"""从我自己的运行内存转储里取 record93 与位图（不依赖捕获 meta.json）。"""
import re, io, sys, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mem = open(os.path.join(ROOT, '.probe/zsec/mem_build.bin'), 'rb').read()
hits = re.findall(rb'[\x20-\x7e]{48}[0-9a-f]{32}z[0-9a-f]{12}', mem)
toks = set(re.findall(rb'005_[A-Za-z0-9+/=]{128}', mem))
if not hits:
    print('内存里没有 record93'); sys.exit(1)
rec = hits[0].decode()
bm = rec[:48]
print('record93 = %s' % rec)
print('位图     = %s' % ' '.join(bm[i:i + 4] for i in range(0, 48, 4)))
print('指纹     = %s' % rec[48:80])
print('crc32    = %s' % rec[81:89])
print('tail4    = %s' % rec[89:])
print('token 数 = %d' % len(toks))
for t in sorted(toks)[:3]:
    print('  tok=%s' % t.decode()[:64])
print()
TGT = '36c020001000800040308000800088008000800080004000'
print('目标(除w0) = %s' % ' '.join(TGT[i:i + 4] for i in range(0, 48, 4)))
diff = []
for i in range(0, 48, 4):
    if i < 4:
        continue
    if bm[i:i + 4] != TGT[i:i + 4]:
        a, b = int(bm[i:i + 4], 16), int(TGT[i:i + 4], 16)
        diff.append('w%d: 我=%04x 真=%04x 多置=%s 少置=%s' % (
            i // 4, a, b, hex(a & ~b), hex(b & ~a)))
print('差异: %s' % ('\n      '.join(diff) if diff else '无（除 w0 外完全一致）'))
