# -*- coding: utf-8 -*-
"""抽 cvm 层（orchestrator）的守卫→置位链（置位进 l[7]/l[8]，即 qh 的 8 位 hex 位图）。"""
import io, re

src = io.open('js/cvm.decompiled.js', encoding='utf-8', errors='replace').read()
lines = src.split('\n')

blocks, cur = [], None
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
order = [b[0] for b in blocks]

def expr(pc):
    """把守卫块压成一行可读表达式"""
    txt = bypc[pc][3]
    parts = []
    for m in re.finditer(r'S\.push\("([^"]{1,40})"\);', txt):
        parts.append(m.group(1))
    for m in re.finditer(r'A\[1\]\s*=\s*"([^"]{1,40})";', txt):
        parts.append('CMP:' + m.group(1))
    for m in re.finditer(r'A\[0\]\s*=\s*"([^"]{1,40})";', txt):
        parts.append(m.group(1))
    return ' | '.join(parts[:12])

sets = []
for pc in order:
    txt = bypc[pc][3]
    for m in re.finditer(r'A\[0\]\s*=\s*1;\s*\n\s*A\[1\]\s*=\s*(\d+);', txt):
        tail = txt[m.end():m.end() + 500]
        mt = re.search(r'A\[0\]\s*=\s*(l\[\d+\]);\s*\n\s*A\[1\]\s*=\s*l\[\d+\];\s*\n\s*A\[3\]\s*=\s*A\[0\]\s*\|\s*A\[1\];', tail)
        if mt:
            sets.append((pc, int(m.group(1)), mt.group(1)))

print('cvm 层置位块:', len(sets))
for i, (pc, shift, tgt) in enumerate(sets):
    j = order.index(pc)
    gp, g = None, ''
    for k in range(j - 1, max(0, j - 8), -1):
        t = bypc[order[k]][3]
        if 'typeof' in t or 'indexOf' in t:
            gp, g = order[k], expr(order[k])
            break
    print('  #%02d %s |= 1<<%-2d  ← 守卫 pc=%-4s  %s' % (i + 1, tgt, shift, gp, g))
