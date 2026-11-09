# -*- coding: utf-8 -*-
"""govm_all.py —— 由 py/gen_all.py + py/js2py.py 从 .probe/wt/zse.decompiled.js
**机械转译**的完整 Go VM（192 个函数）。**不要手改**，改转译器后重跑：

    python py/gen_all.py

控制流已展平成「`_pc` 状态机 + 显式跳转」（混淆器把标签块嵌套到 48 层，
直接嵌套转译会撞 CPython 的 `too many statically nested blocks`）。
"""
import struct
class Brk(Exception):
    def __init__(self, label): self.label = label
class Cont(Exception):
    def __init__(self, label): self.label = label
class Unreachable(Exception): pass
def typeof(x): return type(x).__name__
def _err(x): raise Unreachable(x)
Error = _err
sp = 0
p0 = p1 = p2 = p3 = p4 = p5 = p6 = 0
_h0 = _h1 = _h2 = _h3 = _h4 = _h5 = _h6 = _h7 = _h8 = _h9 = 0
_h10 = _h11 = _h12 = _h13 = _h14 = _h15 = _h16 = _h17 = _h18 = _h19 = 0
_h20 = _h21 = _h22 = _h23 = _h24 = _h25 = _h26 = _h27 = _h28 = _h29 = 0
_h30 = _h31 = _h32 = _h33 = _h34 = _h35 = _h36 = _h37 = _h38 = _h39 = 0
_h40 = _h41 = _h42 = _h43 = _h44 = _h45 = _h46 = _h47 = _h48 = _h49 = 0
_h50 = _h51 = _h52 = _h53 = _h54 = _h55 = _h56 = _h57 = _h58 = _h59 = 0
_h60 = _h61 = _h62 = _h63 = _h64 = _h65 = _h66 = _h67 = _h68 = _h69 = 0
for _i in range(70, 100):
    globals()["_h%d" % _i] = 0
l1 = l2 = l3 = l4 = l5 = l6 = l7 = 0
for _i in range(8, 64):
    globals()["l%d" % _i] = 0
_ifr1 = _ifr2 = _ifr3 = _ifr4 = _ifr5 = _ifr6 = _ifr7 = _ifr8 = 0
_br1 = 0
_h0 = _h1 = _h2 = _h3 = _h4 = _h5 = _h6 = _h7 = _h8 = _h9 = 0
_h10 = _h11 = _h12 = _h13 = _h14 = _h15 = _h16 = _h17 = _h18 = _h19 = 0
_h20 = _h21 = _h22 = _h23 = _h24 = _h25 = _h26 = _h27 = _h28 = _h29 = 0
_h30 = _h31 = _h32 = _h33 = _h34 = _h35 = _h36 = _h37 = _h38 = _h39 = 0
_h40 = _h41 = _h42 = _h43 = _h44 = _h45 = _h46 = _h47 = _h48 = _h49 = 0
_h50 = _h51 = _h52 = _h53 = _h54 = _h55 = _h56 = _h57 = _h58 = _h59 = 0
_h60 = _h61 = _h62 = _h63 = _h64 = _h65 = _h66 = _h67 = _h68 = _h69 = 0
for _i in range(70, 100):
    globals()["_h%d" % _i] = 0
l1 = l2 = l3 = l4 = l5 = l6 = l7 = 0
for _i in range(8, 64):
    globals()["l%d" % _i] = 0
_ifr1 = _ifr2 = _ifr3 = _ifr4 = _ifr5 = _ifr6 = _ifr7 = _ifr8 = 0
_br1 = 0
HOST_LOG = []
def _host(name):
    def f(*a):
        HOST_LOG.append((name, a))
        return 0
    f.__name__ = name
    return f
for _n in ("runtime_fd_write", "runtime_ticks", "syscall_js_copyBytesToGo",
           "syscall_js_finalizeRef", "syscall_js_valueIndex", "syscall_js_valueLength",
           "syscall_js_valueNew", "syscall_js_valueSetIndex"):
    globals()[_n] = _host(_n)
def callIndirect(idx, args=None):
    """elem[idx](*args) —— elem 表由 py/govm_semantics.py 的 elem_of() 建立。"""
    raise NotImplementedError("callIndirect(%r) 需要 elem 表接线" % (idx,))

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


