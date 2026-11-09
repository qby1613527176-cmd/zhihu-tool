#!/usr/bin/env python3
"""Go VM 字节码 → 带**操作数**的 IR（方案 ②）

在 `py/govm_static_ir.py` 的静态走查之上，把每条指令的调用实参**代入实际字求值**，
渲染成可读形式：

    [pc, op, "push r31"]        # _0OO0Oo(p0, 2, 31)  kind=2 -> 寄存器 31
    [pc, op, "push imm 38780"]  # _0OO0Oo(p0, 0, 38780)
    [pc, op, "push pool[138]"]  # _0OO0Oo(p0, 3, 138)
    [pc, op, "disp handler=1"]  # _00oooO(p0, <expr>)

两种解码器族（实测自 `.probe/wt/zse.decompiled.js`）：

  族 A  `_00oooO(p0, <expr>)`          —— 直接算出 handlerId（35 臂语义编号）
  族 B  `_0OO0Oo(p0, <kind>, <value>)` —— 装载一个操作数，kind: 0=立即数 1=局部 2=寄存器 3=字符串池

表达式的字面量映射（实测）：
  `l5`   = prog[pc+1]      （先读的那个字）
  `l1`   = prog[pc+2]
  `M32[(l4 + (l2 << 2))]` = prog[pc+3]

用法
----
  python py/govm_ir_operands.py [--lo A] [--hi B]
"""
import json, struct, re, os, sys, argparse

ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
NORM = lambda x: re.sub(r'[^A-Za-z0-9]', '', x)
KIND = {0: 'imm', 1: 'local', 2: 'r', 3: 'pool'}


def load_prog():
    raw = open(P('.probe', 'zsec', 'prog_live.bin'), 'rb').read()
    return struct.unpack('<%dI' % (len(raw) // 4), raw)


def load_decoders():
    s = open(P('.probe', 'wt', 'zse.decompiled.js'), encoding='utf-8').read()
    fns = {}
    for p in re.split(r'\nfunction ', s):
        m = re.match(r'([\w$]+)\(', p)
        if m:
            fns[m.group(1)] = p
    idx = {NORM(k): v for k, v in fns.items()}
    line = re.search(r'\(elem \$e0[^\n]*', open(P('.probe', 'wt', 'zse.wat'), encoding='utf-8').read()).group()
    names = [x.split('._')[-1].replace('$bound', '') for x in re.findall(r'\$([^\s()]+)', line)][1:]
    return idx, names


def stride_of(body):
    a = re.findall(r'M32\[p0 \+ 20\] = ([^;]+);', body)
    k = None
    if a:
        e = a[-1].strip()
        m = re.match(r'\(l2 \+ (\d+)\)$', e)
        if m:
            k = int(m.group(1))
        if k is None:
            m = re.match(r'\(l(\d+) \+ (\d+)\)$', e)
            if m and (re.search(r'\b' + 'l' + m.group(1) + r' = M32\[p0 \+ 20\]', body)
                      or re.search(r'\b' + 'l' + m.group(1) + r' = \(l2 \+ (\d+)\)', body)):
                k = int(m.group(2))
        if k is None:
            m = re.match(r'(l\d+)$', e)
            if m:
                mm = re.search(r'\b%s = \(l2 \+ (\d+)\)' % m.group(1), body)
                if mm:
                    k = int(mm.group(1))
                else:
                    kk = re.findall(r'M32\[p0 \+ 20\] = \(l(\d+) \+ (\d+)\)', body)
                    if kk:
                        k = int(kk[-1][1]) + 1
    if k is None:
        return 4
    return k if 'callIndirect()' in body else k + 1


def call_expr(body):
    """取出解码器里对 `_0OO0Oo` / `_00oooO` 的调用实参（最后一个）。"""
    ms = list(re.finditer(r'(__main__0oOoOO___0OO0Oo|_00oooO)\(([^;]*?)\);', body))
    if not ms:
        return None, None
    m = ms[-1]
    kind = 'B' if '0OO0Oo' in m.group(1) else 'A'
    args = [x.strip() for x in m.group(2).split(',')]
    if kind == 'A':
        return 'A', args[1] if len(args) > 1 else None      # _00oooO(p0, expr)
    return 'B', args[1:]                                    # _0OO0Oo(p0, kind, value)


def subst(expr, pc, prog, N):
    """把 l5/l1/prog[pc+3] 换成实际数值。"""
    w1 = prog[pc + 1] if pc + 1 < N else 0
    w2 = prog[pc + 2] if pc + 2 < N else 0
    w3 = prog[pc + 3] if pc + 3 < N else 0
    e = expr
    e = e.replace('M32[(l4 + (l2 << 2))]', str(w3))
    e = re.sub(r'\bl5\b', str(w1), e)
    e = re.sub(r'\bl1\b', str(w2), e)
    try:
        return int(eval(e)) & 0xffffffff
    except Exception:
        return None


def render(kind, exprs, pc, prog, N):
    if kind == 'A':
        v = subst(exprs, pc, prog, N)
        return 'disp handler=%s' % (v if v is not None else exprs)
    k = subst(exprs[0], pc, prog, N)
    v = subst(exprs[1], pc, prog, N)
    if k is None or v is None:
        return 'push %s / %s' % (exprs[0], exprs[1])
    if k not in KIND:
        return 'push kind=%d val=%d' % (k, v)
    return 'push %s%s' % (KIND[k], ('[%d]' % v) if k in (2, 3, 1) else (' %d' % v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lo', type=int, default=0)
    ap.add_argument('--hi', type=int, default=None)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    prog = load_prog()
    N = len(prog)
    hi = a.hi if a.hi is not None else N
    idx, names = load_decoders()

    ep = P('.probe', 'zsec', 'bptail.json')
    anchors = sorted({x[3] + 1 for x in json.load(open(ep, encoding='utf-8'))
                      if 0 <= x[3] + 1 < N}) if os.path.exists(ep) else []

    def dec(op):
        t = 105 - op
        if not (1 <= t <= len(names)):
            return None
        return idx.get(NORM('__main__0oOoOO_' + names[t - 1] + '$bound'))

    out = ['# Go VM 字节码 → 带操作数的 IR（锚点对齐走查）', '# 格式: [pc, op, IR]', '']
    pc = a.lo
    n = 0
    while pc < min(hi, N):
        op = prog[pc]
        if op >= 104:
            out.append('[%d, %d, "← 数据"]' % (pc, op))
            pc += 1
            n += 1
            continue
        b = dec(op)
        if b is None:
            out.append('[%d, %d, "?"]' % (pc, op))
            pc += 4
            n += 1
            continue
        kind, exprs = call_expr(b)
        txt = render(kind, exprs, pc, prog, N) if kind else '(%s)' % names[105 - op - 1]
        out.append('[%d, %d, "%s"]' % (pc, op, txt))
        st = stride_of(b)
        nxt = next((x for x in anchors if x > pc), None)
        if nxt is not None and (pc + st > nxt or (pc + st < N and prog[pc + st] >= 104)):
            pc = nxt
        else:
            pc += st
        n += 1

    dest = a.out or P('.probe', 'zsec', 'govm_ir_ops.txt')
    open(dest, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    print('带操作数 IR 条目 %d → %s' % (n, dest))
    return 0


if __name__ == '__main__':
    sys.exit(main())
