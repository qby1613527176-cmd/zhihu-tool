# -*- coding: utf-8 -*-
import re, io

src = io.open('js/service.decompiled.js', 'r', encoding='utf-8').read()
m = re.search(r'const SRC0 = "((?:[^"\\]|\\.)*)";', src)
s = m.group(1)

def unesc(t):
    out = []
    i = 0
    while i < len(t):
        c = t[i]
        if c == '\\':
            n = t[i+1]
            if n == 'u':
                out.append(chr(int(t[i+2:i+6], 16))); i += 6; continue
            if n == 'x':
                out.append(chr(int(t[i+2:i+4], 16))); i += 4; continue
            mp = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f',
                  '0': '\0', '"': '"', '\\': '\\', '/': '/', "'": "'"}
            out.append(mp.get(n, n)); i += 2; continue
        out.append(c); i += 1
    return ''.join(out)

raw = unesc(s)
dec = ''.join(chr(ord(ch) ^ 0x40) for ch in raw)
io.open('.probe/src0.decoded.txt', 'w', encoding='utf-8').write(dec)
print('wrote .probe/src0.decoded.txt len=%d' % len(dec))
print(repr(dec[:60]))
print('...')
print(repr(dec[-80:]))
