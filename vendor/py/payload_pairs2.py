# -*- coding: utf-8 -*-
"""精确配对：守卫块 `if (!F) { pc = X; continue; }` + 紧邻 `pc = Y; continue;` → 置位块 Y。"""
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
    e = blocks[b+1][1] if b+1 < len(blocks) else len(lines)
    blocks[b][2] = '\n'.join(lines[blocks[b][1]:e])
bypc = {b[0]: b[2] for b in blocks}

# 置位块
sets = {}
for pc, t in bypc.items():
    m = re.search(r'A\[0\]\s*=\s*1;\s*\n\s*A\[1\]\s*=\s*(\d+);', t)
    if not m: continue
    tail = t[m.end():m.end() + 600]
    mt = re.search(r'A\[0\]\s*=\s*(l\[\d+\]);\s*\n\s*A\[1\]\s*=\s*l\[\d+\];\s*\n\s*A\[3\]\s*=\s*A\[0\]\s*\|\s*A\[1\];', tail)
    if mt: sets[pc] = (mt.group(1), int(m.group(1)))

# 守卫块（允许换行/空白）
pairs = []
for pc, t in bypc.items():
    for m in re.finditer(r'if \(!F\) \{ pc = (\d+); continue; \}\s*\n\s*pc = (\d+); continue;', t):
        y = int(m.group(2))
        if y in sets:
            pairs.append((pc, y, sets[y][0], sets[y][1], t))
pairs.sort(key=lambda x: (x[2], -x[3]))
print('精确配对 %d 对' % len(pairs))
print()
for pc, y, tgt, sh, t in pairs:
    if tgt not in ('l[4]', 'l[5]', 'l[6]'):
        continue
    keep = []
    for ln in t.split('\n'):
        if re.search(r'S\.push\(|M_EVAL|M_GET|typeof|indexOf|A\[1\]\s*=\s*"|A\[0\]\s*=\s*"|A\[0\]\s*=\s*l\[', ln):
            keep.append(ln.strip())
    print('%s |= 1<<%-2d ← 守卫 pc=%-5d %s' % (tgt, sh, pc, ' ; '.join(keep[:16])))
