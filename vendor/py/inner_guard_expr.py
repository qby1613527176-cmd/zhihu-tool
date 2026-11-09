# -*- coding: utf-8 -*-
"""打印每条守卫块的实质指令，还原出检测表达式。"""
import io, re

src = io.open('js/inner.decompiled.js', encoding='utf-8').read()
lines = src.split('\n')

blocks = []
cur = None
for i, ln in enumerate(lines):
    m = re.match(r'\s*case (\d+): \{', ln)
    if m:
        if cur: blocks.append(cur)
        cur = [int(m.group(1)), i, None, None]
if cur: blocks.append(cur)
for b in range(len(blocks)):
    blocks[b][2] = blocks[b + 1][1] if b + 1 < len(blocks) else len(lines)
    blocks[b][3] = '\n'.join(lines[blocks[b][1]:blocks[b][2]])
bypc = {b[0]: b for b in blocks}

GUARDS = [8, 115, 140, 159, 178, 198, 218, 244, 264, 284, 310, 330, 350, 370,
          400, 409, 428, 447, 466, 485, 504, 523, 542, 561, 580, 599, 618, 637, 656]
for pc in GUARDS:
    if pc not in bypc:
        print('pc=%s 缺块' % pc); continue
    txt = bypc[pc][3]
    keep = []
    for ln in txt.split('\n'):
        if re.search(r'S\.push\(|M_EVAL|M_GET|typeof|A\[3\]\s*=|A\[0\]\s*=\s*"|A\[1\]\s*=|if \(!F\)|pc = \d+; continue', ln):
            keep.append(ln.strip())
    print('--- case %d ---' % pc)
    print('   ' + ' ; '.join(keep[:16]))
