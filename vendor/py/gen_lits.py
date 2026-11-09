# -*- coding: utf-8 -*-
import io, re, json

s = io.open('.probe/zsec/blobs/eval_136041.js', encoding='utf-8').read()
lits = re.findall(r"""'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)\"""", s)
flat = [a or b for a, b in lits]
print('total literals', len(flat))
hits = [x for x in flat if 'process' in x.lower()]
print('process hits:', hits)
for kw in ('require', 'module', 'exports', 'global', 'Buffer', 'window', 'self',
           'globalThis', 'document', 'navigator', 'chrome', 'nsol', 'console'):
    h = [x for x in flat if x == kw]
    if h:
        print('exact', kw, len(h))
# biggest string table candidate: the array with many entries
print('tail 90 literals:', json.dumps(flat[-90:], ensure_ascii=False))
# find the decoder body
i = s.find('function _0x4811')
print('--- _0x4811 def ctx ---')
print(s[i:i+700])
