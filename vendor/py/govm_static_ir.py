#!/usr/bin/env python3
"""Go VM 字节码 → **静态线性 IR**（不跑 wasm，只看字节）

    [pc, op, handlerId, K, "IR 文本"]

- `pc`        = 词下标
- `op`        = `prog[pc]`（0..103 = 表索引；≥104 判为「数据/失步」）
- `handlerId` = 35 臂语义编号（实测表；未知则 `?`）
- `K`         = 该 op 的**指令长度**（词数，来自 `$bound` 解码器的最终 `M32[p0+20] = l2 + K`）
- `IR 文本`   = 臂体里最关键的语句（`.probe/zsec/govm_handlers.json`）

**跳转一概不管**：遇到 br/jump 也无所谓，我们只按 `K` 线性推进、把每条指令列出来。
跳转目标在 IR 里表现为「下一行 pc 不连续」，这不影响阅读。

指令长度表（实测）：K=2 有 1 个方法、K=3 有 60 个、K=4 有 7 个；其余 36 个方法
（尾分派/叶子型）没有显式 pc 赋值 ⇒ 用默认 3。

用法
----
  python py/govm_static_ir.py                      # 全程序
  python py/govm_static_ir.py --lo 10300 --hi 10560
"""
import json, struct, re, os, sys, argparse

ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
NORM = lambda x: re.sub(r'[^A-Za-z0-9]', '', x)
DEFAULT_K = 3


def load_prog():
    raw = open(P('.probe', 'zsec', 'prog_live.bin'), 'rb').read()
    return struct.unpack('<%dI' % (len(raw) // 4), raw)


def _length(body):
    assigns = re.findall(r'M32\[p0 \+ 20\] = ([^;]+);', body)
    if not assigns or not re.search(r'l2 = M32\[p0 \+ 20\]', body):
        return None
    e = assigns[-1].strip()
    m = re.match(r'\(l2 \+ (\d+)\)$', e)
    if m:
        return int(m.group(1))
    m = re.match(r'(l\d+)$', e)
    if m:
        mm = re.search(r'\b%s = \(l2 \+ (\d+)\)' % m.group(1), body)
        if mm:
            return int(mm.group(1))
    return None


def build_tables():
    """(1) 104 个方法的指令长度  (2) elem 顺序  (3) 35 臂 IR 摘要  (4) op→handlerId 实测"""
    s = open(P('.probe', 'wt', 'zse.decompiled.js'), encoding='utf-8').read()
    fns = {}
    for p in re.split(r'\nfunction ', s):
        m = re.match(r'([\w$]+)\(', p)
        if m:
            fns[m.group(1)] = p
    K = {}
    for k, v in fns.items():
        if '$bound' in k:
            K[NORM(k.split('__main__0oOoOO_')[-1].replace('$bound', ''))] = _length(v)

    line = re.search(r'\(elem \$e0[^\n]*', open(P('.probe', 'wt', 'zse.wat'), encoding='utf-8').read()).group()
    order = []
    for x in re.findall(r'\$_([^\s)]+)', line):
        y = x.split('._')[-1]
        if y.endswith('$bound'):
            y = y[:-6]
        order.append(NORM(y))

    handlers = {}
    hp = P('.probe', 'zsec', 'govm_handlers.json')
    if os.path.exists(hp):
        for k, v in json.load(open(hp, encoding='utf-8')).items():
            code = v.get('code', '') if isinstance(v, dict) else str(v)
            lines = [l.strip() for l in code.split('\n') if l.strip()]
            pick = [l for l in lines
                    if re.search(r'runtime_\w+\(|M32\[\(p0 \+ 276\)\]|M32\[\(p0 \+ 280\)\]', l)][:2]
            handlers[int(k)] = ' ; '.join(pick) if pick else (lines[0] if lines else '')

    op2h = {}
    ep = P('.probe', 'zsec', 'bptail.json')
    if os.path.exists(ep):
        import collections
        prog = load_prog()
        m = collections.defaultdict(collections.Counter)
        for x in json.load(open(ep, encoding='utf-8')):
            p = x[3]
            if 0 <= p + 1 < len(prog):
                m[prog[p + 1]][x[1]] += 1
        op2h = {op: c.most_common(1)[0][0] for op, c in m.items()}
    return K, order, handlers, op2h


def kof(op, order, K):
    """op → 指令长度。表索引 = 105-op（wasm 实测），elem 基址 1 ⇒ 列表下标 104-op。"""
    i = 104 - op
    if 0 <= i < len(order):
        k = K.get(order[i])
        if k:
            return k, order[i]
    return DEFAULT_K, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lo', type=int, default=0)
    ap.add_argument('--hi', type=int, default=None)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    prog = load_prog()
    N = len(prog)
    hi = a.hi if a.hi is not None else N
    K, order, handlers, op2h = build_tables()

    out = ['# Go VM 字节码 → 静态线性 IR（跳转一概不管）',
           '# 格式: [pc, op, handlerId, K, "IR"]   op>=104 判为数据/失步',
           '']
    pc = a.lo
    n = good = 0
    while pc < min(hi, N):
        op = prog[pc]
        if op >= 104:
            out.append('[%d, %d, -, -, "← 数据/失步"]' % (pc, op))
            pc += 1
            n += 1
            continue
        k, meth = kof(op, order, K)
        hid = op2h.get(op)
        out.append('[%d, %d, %s, %d, "%s"]' % (pc, op, hid if hid is not None else '?', k,
                                               handlers.get(hid, meth or '')[:150]))
        pc += k
        n += 1
        good += 1
    dest = a.out or P('.probe', 'zsec', 'govm_static_ir.txt')
    open(dest, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    print('IR 条目 %d（合法 op %d，%.1f%%） → %s' % (n, good, 100.0 * good / max(n, 1), dest))
    print()
    print('指令长度表（op -> K）：')
    ks = {}
    for op in range(104):
        k, m = kof(op, order, K)
        ks.setdefault(k, []).append(op)
    for k in sorted(ks):
        print('   K=%d: %d 个 op' % (k, len(ks[k])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
