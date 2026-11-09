# -*- coding: utf-8 -*-
"""逐块抽 inner 层置位链的「守卫 → 置位」对应关系（只用已还原产物 js/inner.decompiled.js）。"""
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
order = [b[0] for b in blocks]

def describe(pc):
    """把块里的检测表达式压成一行"""
    txt = bypc[pc][3]
    out = []
    # 属性名
    for m in re.finditer(r'S\.push\("([^"]{1,40})"\);', txt):
        out.append(m.group(1))
    # 字符串比较
    for m in re.finditer(r'A\[1\]\s*=\s*"([^"]{1,40})";', txt):
        out.append('==' + m.group(1))
    for m in re.finditer(r'A\[0\]\s*=\s*"([^"]{1,40})";', txt):
        out.append(m.group(1))
    return ' | '.join(out[:10])

# 找置位块
sets = []
for pc in order:
    txt = bypc[pc][3]
    for m in re.finditer(r'A\[0\]\s*=\s*1;\s*\n\s*A\[1\]\s*=\s*(\d+);', txt):
        tail = txt[m.end():m.end() + 400]
        mt = re.search(r'\n\s*(l\[3\]|l\[4\])\s*=\s*A\[3\];', tail)
        if mt:
            sets.append((pc, int(m.group(1)), mt.group(1)))

print('置位块:', len(sets))
for i, (pc, shift, tgt) in enumerate(sets):
    # 守卫 = 链上紧邻的前一个「含检测」块
    j = order.index(pc)
    guard_pc, guard = None, ''
    for k in range(j - 1, max(0, j - 6), -1):
        d = describe(order[k])
        if 'indexOf' in bypc[order[k]][3] or 'typeof' in bypc[order[k]][3]:
            guard_pc, guard = order[k], d
            break
    print('  #%02d %s |= 1<<%-2d  ← 守卫 pc=%-4s  %s' % (i + 1, tgt, shift, guard_pc, guard))
