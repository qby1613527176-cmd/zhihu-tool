#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""govm_py_run.py —— 用**纯 Python** 跑 Go VM（`py/govm_all.py`），目标是零 wasm / 零 Node。

现在做到哪一步
--------------
· 数据段 + 程序字节装入内存         ✅
· `callIndirect` 按 elem 表接线      ✅（elem 名来自 .probe/zsec/_table.json，`$`→`_`）
· 192/192 个函数（含 4 个语义派发器）✅ 已在 Python 里
· 宿主面（syscall/js + runtime_fd_write/ticks）⚠️ 记录型桩 —— **真正接上 JS 侧的值是剩下的最后一块**

用法
----
  python py/govm_py_run.py                 # 装内存 + 调 _start，报告跑到哪里、第一个缺什么
  python py/govm_py_run.py --pc 76 --op 14 # 只跑某一条指令（调试用）
"""
import os, sys, re, json, argparse, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
P = lambda *a: os.path.join(ROOT, *a)


def load(out='out'):
    sys.path.insert(0, P('py'))
    import govm_all as g
    # ---- elem 表：callIndirect(idx) -> _table.json[idx]（名字里的 $ 换成 _）
    tbl = json.load(open(P('.probe', 'zsec', '_table.json'), encoding='utf8'))

    def callIndirect(idx, args=None):
        nm = (tbl.get(str(idx)) or '').replace('$', '_')
        f = getattr(g, nm, None)
        if f is None:
            raise NotImplementedError('callIndirect(%r): 表项 %r 未定义' % (idx, nm))
        g.HOST_LOG.append(('callIndirect', (idx, nm)))
        return f(*(args or []))
    g.callIndirect = callIndirect
    # ---- 数据段 + 程序字节
    ds = json.load(open(P('.probe', 'zsec', '_datasegs.json'), encoding='utf8'))
    for addr, hx in ds:
        b = bytes.fromhex(hx)
        g.MEM.b[int(addr):int(addr) + len(b)] = b
    prog = open(os.path.join(out, 'prog.bin'), 'rb').read()
    PROG_PTR = 0x400000
    for i, b in enumerate(prog):
        g.MEM.b[PROG_PTR + (i << 2)] = b
    return g, ds, prog, PROG_PTR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=P('out'))
    ap.add_argument('--pc', type=int, default=None)
    ap.add_argument('--op', type=int, default=None)
    ap.add_argument('--vm', type=lambda x: int(x, 0), default=314288)
    a = ap.parse_args()
    g, ds, prog, PROG_PTR = load(a.out)
    print('[run] 数据段 %d 个；程序 %d 词 @ %#x；VM 结构体 @ %#x' % (len(ds), len(prog), PROG_PTR, a.vm))

    if a.pc is not None:
        g.M32[a.vm + 20] = a.pc
        g.M32[a.vm + 308] = PROG_PTR
        g.M32[a.vm + 312] = len(prog)
        g.M32[a.vm + 380] = 104
        g.M32[a.vm + 376] = 0x500000
        try:
            r = g.__main__0oOoOO___00oooO(a.vm, a.op or 0)
            print('[run] 派发器返回 %r' % (r,))
        except Exception as e:
            print('[run] 派发器抛 %s: %s' % (type(e).__name__, str(e)[:120]))
    else:
        f = getattr(g, '_start', None)
        if f is None:
            print('[run] 找不到 _start')
            return
        try:
            r = f()
            print('[run] _start 正常返回 %r' % (r,))
        except Exception as e:
            print('[run] _start 抛 %s: %s' % (type(e).__name__, str(e)[:160]))
    print('[run] 宿主调用 %d 次，前 8 条:' % len(g.HOST_LOG))
    for x in g.HOST_LOG[:8]:
        print('       ', x)


if __name__ == '__main__':
    main()
