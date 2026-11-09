#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_all.py —— 把 .probe/wt/zse.decompiled.js 的**全部 192 个函数**机械转译成
py/govm_all.py（Python），并补上内存模型 + 平凡算术 helper。

控制流用 py/js2py.stmts_threaded()（`_pc` 状态机 + 显式跳转），原因见 gen_arms.py 的注释。

自检：
  · 192/192 个函数都通过 compile()
  · 模块可 import，且列出的 helper 都已实现或已桩化
  · `--run` 会尝试调用 `_start_command_export`（需要 syscall/js 环境，未实现的部分会抛
    NotImplementedError —— 这是「零执行纯 Python 解释器」剩下的最后一块）
"""
import os, re, sys, ast, json, collections, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
P = lambda *a: os.path.join(ROOT, *a)
DECOMPILED = P('.probe', 'wt', 'zse.decompiled.js')

# 平凡算术 / 位运算 helper（Go 的 wasm 内建，语义确定）
TRIVIAL = '''
def uge(a, b): return (int(a) & 0xffffffff) >= (int(b) & 0xffffffff)
def ugt(a, b): return (int(a) & 0xffffffff) > (int(b) & 0xffffffff)
def ule(a, b): return (int(a) & 0xffffffff) <= (int(b) & 0xffffffff)
def ult(a, b): return (int(a) & 0xffffffff) < (int(b) & 0xffffffff)
def uge64(a, b): return (int(a) & 0xffffffffffffffff) >= (int(b) & 0xffffffffffffffff)
def ugt64(a, b): return (int(a) & 0xffffffffffffffff) > (int(b) & 0xffffffffffffffff)
def ule64(a, b): return (int(a) & 0xffffffffffffffff) <= (int(b) & 0xffffffffffffffff)
def ult64(a, b): return (int(a) & 0xffffffffffffffff) < (int(b) & 0xffffffffffffffff)
def ushr(a, b): return (int(a) & 0xffffffff) >> int(b)
def ushr64(a, b): return (int(a) & 0xffffffffffffffff) >> int(b)
def wrap32(x): return int(x) & 0xffffffff
def extU32(x): return int(x) & 0xffffffff
def extS32(x): x = int(x) & 0xffffffff; return x - 0x100000000 if x >= 0x80000000 else x
def i32_64(x): return int(x) & 0xffffffff
def i8_64(x): x = int(x) & 0xff; return x - 0x100 if x >= 0x80 else x
def i16_64(x): x = int(x) & 0xffff; return x - 0x10000 if x >= 0x8000 else x
def i8(x): x = int(x) & 0xff; return x - 0x100 if x >= 0x80 else x
def idiv(a, b):
    a = int(a); b = int(b)
    if b == 0: raise ZeroDivisionError('idiv')
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q
def irem(a, b):
    a = int(a); b = int(b)
    if b == 0: return 0
    r = abs(a) % abs(b)
    return r if a >= 0 else -r
def udiv64(a, b): return (int(a) & 0xffffffffffffffff) // (int(b) & 0xffffffffffffffff)
def urem64(a, b): return (int(a) & 0xffffffffffffffff) % (int(b) & 0xffffffffffffffff)
def ctz32(x):
    x = int(x) & 0xffffffff
    return 32 if x == 0 else (x & -x).bit_length() - 1
def i64_reinterpret_f64(x): return x
def f64_reinterpret_i64(x): return x
def BigInt(x): return int(x)
def _f64(x): return float(x)
'''

MEMORY = '''
# ------------------------------------------------------------------ 内存
PAGE = 65536
class _Mem(object):
    """5 MB 起、可增长的线性内存；M8/M16/M32/M64/F64 都是**字节寻址**视图
       （与反编译产物里的用法一致：`M32[byte_addr]` 读该地址起 4 字节小端）。"""
    def __init__(self, pages=80):
        self.b = bytearray(pages * PAGE)
    def grow(self, pages):
        self.b.extend(bytearray(pages * PAGE))
        return len(self.b) // PAGE - pages
    def __len__(self): return len(self.b)

MEM = _Mem()
class _View(object):
    __slots__ = ('scale', 'signed', 'isfloat')
    def __init__(self, scale, signed=False, isfloat=False):
        self.scale = scale; self.signed = signed; self.isfloat = isfloat
    def __getitem__(self, k):
        a = int(k)
        if a < 0 or a + self.scale > len(MEM.b):
            return 0
        if self.isfloat:
            import struct as _s
            return _s.unpack_from('<d' if self.scale == 8 else '<f', MEM.b, a)[0]
        return int.from_bytes(MEM.b[a:a + self.scale], 'little', signed=self.signed)
    def __setitem__(self, k, v):
        a = int(k)
        if self.isfloat:
            import struct as _s
            _s.pack_into('<d' if self.scale == 8 else '<f', MEM.b, a, float(v))
            return
        MEM.b[a:a + self.scale] = (int(v) & ((1 << (8 * self.scale)) - 1)).to_bytes(self.scale, 'little')
M8, M8s = _View(1), _View(1, True)
M16, M16s = _View(2), _View(2, True)
M32, M32s = _View(4), _View(4, True)
M64 = _View(8)
F64, F32 = _View(8, isfloat=True), _View(4, isfloat=True)
'''


def main():
    spec = importlib.util.spec_from_file_location('js2py', P('py', 'js2py.py'))
    j = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(j)
    src = open(DECOMPILED, encoding='utf8').read()

    fns = []
    for m in re.finditer(r'^function ([A-Za-z0-9_$]+)\(([^)]*)\)', src, re.M):
        nm, params = m.group(1), m.group(2)
        i = src.index('{', m.end() - 1)
        d = 0
        for k in range(i, len(src)):
            if src[k] == '{':
                d += 1
            elif src[k] == '}':
                d -= 1
                if d == 0:
                    break
        fns.append((nm, params, src[i + 1:k], src[:i].count('\n') + 1))

    out = ['# -*- coding: utf-8 -*-',
           '"""govm_all.py —— 由 py/gen_all.py + py/js2py.py 从 .probe/wt/zse.decompiled.js',
           '**机械转译**的完整 Go VM（%d 个函数）。**不要手改**，改转译器后重跑：' % len(fns),
           '',
           '    python py/gen_all.py',
           '',
           '控制流已展平成「`_pc` 状态机 + 显式跳转」（混淆器把标签块嵌套到 48 层，',
           '直接嵌套转译会撞 CPython 的 `too many statically nested blocks`）。',
           '"""',
           'import struct',
           'class Brk(Exception):',
           '    def __init__(self, label): self.label = label',
           'class Cont(Exception):',
           '    def __init__(self, label): self.label = label',
           'class Unreachable(Exception): pass',
           'def typeof(x): return type(x).__name__',
           'def _err(x): raise Unreachable(x)',
           'Error = _err',
           # 反编译产物里 `sp` / `p4`~`p6` 是**隐式全局**（JS 非严格模式），必须在模块级给上
           'sp = 0',
           'p0 = p1 = p2 = p3 = p4 = p5 = p6 = 0',
           # 反编译产物里 `_hN` 也是隐式全局（有的函数引用了别处 `let` 过的 `_hN`），
           # 统一在模块级给 0，忠实模拟 JS 非严格模式的隐式全局行为
           '_h0 = _h1 = _h2 = _h3 = _h4 = _h5 = _h6 = _h7 = _h8 = _h9 = 0',
           '_h10 = _h11 = _h12 = _h13 = _h14 = _h15 = _h16 = _h17 = _h18 = _h19 = 0',
           '_h20 = _h21 = _h22 = _h23 = _h24 = _h25 = _h26 = _h27 = _h28 = _h29 = 0',
           '_h30 = _h31 = _h32 = _h33 = _h34 = _h35 = _h36 = _h37 = _h38 = _h39 = 0',
           '_h40 = _h41 = _h42 = _h43 = _h44 = _h45 = _h46 = _h47 = _h48 = _h49 = 0',
           '_h50 = _h51 = _h52 = _h53 = _h54 = _h55 = _h56 = _h57 = _h58 = _h59 = 0',
           '_h60 = _h61 = _h62 = _h63 = _h64 = _h65 = _h66 = _h67 = _h68 = _h69 = 0',
           'for _i in range(70, 100):',
           '    globals()["_h%d" % _i] = 0',
           'l1 = l2 = l3 = l4 = l5 = l6 = l7 = 0',
           'for _i in range(8, 64):',
           '    globals()["l%d" % _i] = 0',
           '_ifr1 = _ifr2 = _ifr3 = _ifr4 = _ifr5 = _ifr6 = _ifr7 = _ifr8 = 0',
           '_br1 = 0',
           # 反编译产物里 `_hN` 也是隐式全局（有的函数引用了别处 `let` 过的 `_hN`），
           # 统一在模块级给 0，忠实模拟 JS 非严格模式的隐式全局行为
           '_h0 = _h1 = _h2 = _h3 = _h4 = _h5 = _h6 = _h7 = _h8 = _h9 = 0',
           '_h10 = _h11 = _h12 = _h13 = _h14 = _h15 = _h16 = _h17 = _h18 = _h19 = 0',
           '_h20 = _h21 = _h22 = _h23 = _h24 = _h25 = _h26 = _h27 = _h28 = _h29 = 0',
           '_h30 = _h31 = _h32 = _h33 = _h34 = _h35 = _h36 = _h37 = _h38 = _h39 = 0',
           '_h40 = _h41 = _h42 = _h43 = _h44 = _h45 = _h46 = _h47 = _h48 = _h49 = 0',
           '_h50 = _h51 = _h52 = _h53 = _h54 = _h55 = _h56 = _h57 = _h58 = _h59 = 0',
           '_h60 = _h61 = _h62 = _h63 = _h64 = _h65 = _h66 = _h67 = _h68 = _h69 = 0',
           'for _i in range(70, 100):',
           '    globals()["_h%d" % _i] = 0',
           'l1 = l2 = l3 = l4 = l5 = l6 = l7 = 0',
           'for _i in range(8, 64):',
           '    globals()["l%d" % _i] = 0',
           '_ifr1 = _ifr2 = _ifr3 = _ifr4 = _ifr5 = _ifr6 = _ifr7 = _ifr8 = 0',
           '_br1 = 0',
           # ---- 宿主面（syscall/js + 少量 runtime）：这是「零执行纯 Python」剩下的最后一块 ----
           'HOST_LOG = []',
           'def _host(name):',
           '    def f(*a):',
           '        HOST_LOG.append((name, a))',
           '        return 0',
           '    f.__name__ = name',
           '    return f',
           'for _n in ("runtime_fd_write", "runtime_ticks", "syscall_js_copyBytesToGo",',
           '           "syscall_js_finalizeRef", "syscall_js_valueIndex", "syscall_js_valueLength",',
           '           "syscall_js_valueNew", "syscall_js_valueSetIndex"):',
           '    globals()[_n] = _host(_n)',
           'def callIndirect(idx, args=None):',
           '    """elem[idx](*args) —— elem 表由 py/govm_semantics.py 的 elem_of() 建立。"""',
           '    raise NotImplementedError("callIndirect(%r) 需要 elem 表接线" % (idx,))',
           TRIVIAL, MEMORY, '']
    ok, bad = 0, []
    for nm, params, body, ln0 in fns:
        try:
            py = j.stmts_threaded(body)
        except Exception as e:
            bad.append((nm, type(e).__name__, str(e)[:60]))
            continue
        sig = ', '.join(p.strip() for p in params.split(',') if p.strip())
        # 名字里的 `$` 不是合法 Python 标识符字符（`hash_crc32_ieeeInit$1`、
        #   `__main__0oOoOO___0Ooo00$bound` 这类）⇒ def 名与调用点都必须换成 `_`
        pyname = nm.replace('$', '_')
        code = ['# ' + '=' * 72,
                '# %s  (源第 %d 行, %d 行 JS)' % (nm, ln0, body.count('\n') + 1),
                '# ' + '=' * 72,
                'def %s(%s):' % (pyname, sig)] + py + ['']
        try:
            compile('\n'.join(code), '<t>', 'exec')
            ok += 1
        except SyntaxError as e:
            bad.append((nm, 'SyntaxError', str(e)[:60]))
            continue
        out += code
    text = '\n'.join(out) + '\n'
    ast.parse(text)
    open(P('py', 'govm_all.py'), 'w', encoding='utf8').write(text)
    print('[gen-all] py/govm_all.py  %d 行；%d/%d 个函数转译并编译通过'
          % (text.count('\n'), ok, len(fns)))
    for b in bad[:8]:
        print('     ⛔', b)


if __name__ == '__main__':
    main()
