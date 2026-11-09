# -*- coding: utf-8 -*-
"""抽 payload 层 l[4]/l[5]/l[6] 组守卫块的实质指令（还原检测表达式）。"""
import io, re

src = io.open('js/payload.cvm.js', encoding='utf-8', errors='replace').read()
lines = src.split('\n')
blocks, cur = [], None
for i, ln in enumerate(lines):
    m = re.match(r'\s*case (\d+): \{', ln)
    if m:
        if cur: blocks.append(cur)
        cur = [int(m.group(1)), i, None]
if cur: blocks.append(cur)
for b in range(len(blocks)):
    e = blocks[b + 1][1] if b + 1 < len(blocks) else len(lines)
    blocks[b][2] = '\n'.join(lines[blocks[b][1]:e])
bypc = {b[0]: b[2] for b in blocks}

# 置位块 → 其前置守卫块
SETS = []
for pc in sorted(bypc):
    t = bypc[pc]
    for m in re.finditer(r'A\[0\]\s*=\s*1;\s*\n\s*A\[1\]\s*=\s*(\d+);', t):
        tail = t[m.end():m.end() + 600]
        mt = re.search(r'A\[0\]\s*=\s*(l\[\d+\]);\s*\n\s*A\[1\]\s*=\s*l\[\d+\];\s*\n\s*A\[3\]\s*=\s*A\[0\]\s*\|\s*A\[1\];', tail)
        if mt:
            SETS.append((pc, int(m.group(1)), mt.group(1)))

order = sorted(bypc)
print('置位块 %d 个' % len(SETS))
for i, (pc, shift, tgt) in enumerate(SETS):
    if tgt not in ('l[4]', 'l[5]', 'l[6]'):
        continue
    j = order.index(pc)
    gp = None
    for k in range(j - 1, max(0, j - 8), -1):
        tt = bypc[order[k]]
        if 'typeof' in tt or 'indexOf' in tt:
            gp = order[k]; break
    g = bypc.get(gp, '')
    keep = []
    for ln in g.split('\n'):
        if re.search(r'S\.push\(|M_EVAL|M_GET|typeof|indexOf|A\[1\]\s*=\s*"|A\[0\]\s*=\s*"', ln):
            keep.append(ln.strip())
    print('#%02d %s |= 1<<%-2d ← 守卫 pc=%-4s %s' % (i + 1, tgt, shift, gp, ' ; '.join(keep[:14])))
