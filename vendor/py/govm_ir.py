#!/usr/bin/env python3
"""Go VM 字节码 → 线性 IR（照「wasm 分析」那套方法）

思路（关键：**不还原 JS，不处理跳转**）
--------------------------------------
对每条**已执行**的指令，只输出 `[pc, op, handlerId, IR 文本]`：

    [519712, 2, 9, "r21 = -1212248019"]

- `pc`        = 该指令取指处的词下标
- `op`        = `prog[pc]`（0..103，表索引）
- `handlerId` = 送进 `_00oooO` 的 p1（0..34，35 臂语义）
- `IR 文本`   = 该 handler 臂体里最关键的一两行（从 `.probe/zsec/govm_handlers.json` 抽）

**跳转一概不管**：pc 会乱跳，但我们只是把「执行过的指令」按发生顺序列出来，
读起来比字节码强得多，也比硬还原 JS 省事得多。

数据来源
--------
- `prog_live.bin`                     程序（10565 个 u32 词）
- `.probe/zsec/bptail.json`           入口插桩：`[p0, acc(p1), p0, entry_pc]`，2446 条
- `.probe/zsec/govm_handlers.json`    35 个语义臂体
- `.probe/zsec/govm_decode_table.json` 104 个 `$bound` 解码器

用法
----
  python py/govm_ir.py                       # 全部 2446 条 → .probe/zsec/govm_ir.txt
  python py/govm_ir.py --lo 10300 --hi 10560 # 只看某个 pc 区间
"""
import json, struct, re, os, sys, argparse

ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)


def load_prog():
    raw = open(P('.probe', 'zsec', 'prog_live.bin'), 'rb').read()
    return struct.unpack('<%dI' % (len(raw) // 4), raw)


def load_handlers():
    """35 臂 → IR 摘要（取臂体里最像『做了什么』的一两行）。"""
    p = P('.probe', 'zsec', 'govm_handlers.json')
    if not os.path.exists(p):
        return {}
    h = json.load(open(p, encoding='utf-8'))
    out = {}
    for k, v in h.items():
        code = v.get('code', '') if isinstance(v, dict) else str(v)
        lines = [l.strip() for l in code.split('\n') if l.strip()]
        # 摘要：跳过弹栈样板，取第一条「有实质动作」的语句
        pick = []
        for l in lines:
            if re.search(r'runtime_\w+\(|M32\[\(p0 \+ 276\)\]|M32\[\(p0 \+ 280\)\]|= M32\[\(p0 \+ 300\)\]', l):
                pick.append(l)
            if len(pick) >= 2:
                break
        out[int(k)] = ' ; '.join(pick) if pick else (lines[0] if lines else '')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lo', type=int, default=None)
    ap.add_argument('--hi', type=int, default=None)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    prog = load_prog()
    N = len(prog)
    ep = P('.probe', 'zsec', 'bptail.json')
    if not os.path.exists(ep):
        print('缺 %s —— 本工具需要「入口插桩」轨迹（[p0, acc(p1), p0, entry_pc]）。' % ep)
        print('它由真机 wasm 在 `_00oooO` 入口（file_off 0xE0E9）插桩跑出：')
        print('  python py/wasm_patch_raw.py .probe/build-859e.wasm out.wasm 0xE0E9 1')
        print('没有它请改用不依赖轨迹的静态版：python py/govm_static_ir.py')
        return 2
    ev = json.load(open(ep, encoding='utf-8'))
    handlers = load_handlers()

    # op -> handlerId（实测：按 prog[entry_pc+1] 对齐，取主值）
    import collections
    m = collections.defaultdict(collections.Counter)
    for x in ev:
        p = x[3]
        if 0 <= p + 1 < N:
            m[prog[p + 1]][x[1]] += 1
    op2h = {op: c.most_common(1)[0][0] for op, c in m.items()}

    out = []
    out.append('# Go VM 字节码 → 线性 IR（跳转一概不管）')
    out.append('# 格式: [pc, op, handlerId, IR]')
    out.append('# op→handlerId 来自入口插桩实测（2446 条）')
    out.append('')
    n = 0
    for x in ev:
        p = x[3]
        if a.lo is not None and p < a.lo:
            continue
        if a.hi is not None and p > a.hi:
            continue
        if not (0 <= p + 1 < N):
            continue
        op = prog[p + 1]
        hid = op2h.get(op)
        ir = handlers.get(hid, '')
        out.append('[%d, %d, %s, "%s"]' % (p + 1, op, hid, ir[:160]))
        n += 1
    txt = '\n'.join(out) + '\n'
    dest = a.out or P('.probe', 'zsec', 'govm_ir.txt')
    open(dest, 'w', encoding='utf-8').write(txt)
    print('IR 条目: %d  → %s' % (n, dest))
    print()
    print('op → handlerId 实测表（%d 个 op）:' % len(op2h))
    for op in sorted(op2h):
        print('   op=%-4d -> handler %-3d  (%d 条)' % (op, op2h[op], sum(m[op].values())))
    return 0


if __name__ == '__main__':
    sys.exit(main())
