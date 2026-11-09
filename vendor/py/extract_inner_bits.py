# -*- coding: utf-8 -*-
"""从 js/inner.decompiled.js 抽出置位链：每个 case 块里 (1<<N) 累加进 l[3] / l[4] 的位置 + 同块的 typeof 守卫。"""
import io, re, json

src = io.open('js/inner.decompiled.js', encoding='utf-8').read()
lines = src.split('\n')

# 按 case 分块
blocks = []          # (pc, start_line, end_line, text)
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

print('总 case 块:', len(blocks))

# 在每块里找：A[1] = N;  ...  l[3] = A[3];  /  l[4] = A[3];
rows = []
for pc, s, e, txt in blocks:
    # 找所有 A[1] = <num>; 后面跟 l[3]/l[4] = A[3];
    for m in re.finditer(r'A\[0\]\s*=\s*1;\s*\n\s*A\[1\]\s*=\s*(\d+);', txt):
        shift = int(m.group(1))
        tail = txt[m.end():m.end() + 400]
        tgt = None
        mt = re.search(r'\n\s*(l\[\d+\])\s*=\s*A\[3\];', tail)
        if mt: tgt = mt.group(1)
        # 守卫：本块里出现的 typeof ... === "undefined" 的对象名 / 字符串常量
        guards = re.findall(r'A\[0\]\s*=\s*"([^"]{1,40})";', txt)
        rows.append((pc, shift, tgt, guards[:6]))

print('置位点:', len(rows))
for pc, shift, tgt, guards in rows:
    print('  pc=%-4d shift=%-3d -> %-6s guards=%s' % (pc, shift, tgt, guards))
