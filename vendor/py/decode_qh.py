# -*- coding: utf-8 -*-
"""解码 qh 的前 8 字符 = cvm 层 l[7].toString(16) + l[8].toString(16)。
编码：out_i = (c_i ^ k_i) ^ out_{i-1}，out_{-1} = acc0（= __b.a3）
⇒ c_i = out_i ^ k_i ^ out_{i-1}  (i>=1)，c_0 由 acc0 定；用「c_0 必须是 hex 字符」定 acc0。"""

KEY = '6a6cb1bb3970bae6'

def decode_qh_head(enc8):
    out = [ord(ch) for ch in enc8]
    k = [ord(ch) for ch in KEY]
    # c_i for i>=1 与 acc0 无关
    c = [None] * len(out)
    for i in range(1, len(out)):
        c[i] = out[i] ^ k[i % len(k)] ^ out[i - 1]
    cands = []
    for acc0 in range(256):
        c0 = out[0] ^ k[0] ^ acc0
        ch = chr(c0)
        if ch in '0123456789abcdef':
            cands.append((acc0, ch + ''.join(chr(x) for x in c[1:])))
    return cands

MINE = "'vp#ut&t"          # 我 zh/qh 日志里的前 8 字符
REAL = "|-+x./}/"          # 真浏览器 __c.qh 的前 8 字符

print('我的 qh 前8字符 =', repr(MINE))
print('真   qh 前8字符 =', repr(REAL))
print()
for tag, enc in (('我', MINE), ('真', REAL)):
    cs = decode_qh_head(enc)
    print('%s 候选（acc0, l[7]+l[8] 的 hex 形态）: %s' % (tag, cs))
print()
print('对照：真浏览器 record93 位图 = b903 2000 1000 8000 4000 8000 8000 8800 8000 8000 8000 4000')
print('     我的 record93 位图       = dbf6 202b 1000 8000 6830 8000 8000 9800 8a00 8000 c000 4000')
