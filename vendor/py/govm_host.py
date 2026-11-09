#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""govm_host.py —— Go VM 的**宿主层**（syscall/js）纯 Python 实现

对应 `py/govm_pure_vm.js` 里那套已经验收过的东西，语义照搬：
  · NaN-box 值池：`_vals` 0..5 保留（1=undefined 2=null 3=true 4=false 5=global）
  · `storeValue` / `loadValue`：把宿主值写进 / 读出 Go 线性内存
  · `boxDecode(bits)` / `makeRef(v)`
  · `copyBytesToGo(sp, op, ...)` —— 那个**按 op 分派**的大 switch：
        1022 Value.Set      `Reflect.set(v7, key, val)`
        1024 Value.Get      `Reflect.get(v7, prop)`
        1026 Value.Call     `Reflect.get(v7,name).apply(v7, args)` → storeValue
        1028 Value.String   `encode(String(v7))` → storeValue + 长度
        1030 copyBytesToGo  `dst[10] ← v7[.. len]`
        1032 / 1034         Invoke / New（同族，见下）
  · 一个**最小 JS 对象模型**（`JSObj` / 全局对象 / 内建构造器），够 Go 的
    `js.Value` 机制初始化并往下走。

这不是完整的浏览器环境：`window` / `document` / `navigator` / 载荷那部分仍需补
（JS 侧已有 `handenv.js`，这里只提供 Go 运行时初始化真正需要的那批内建）。
"""
import math, json

NANHEAD = 0x7FF80000
CODE = {'object': 1, 'string': 2, 'symbol': 3, 'function': 4}


# ---------------------------------------------------------------- JS 对象模型
class JSObj(object):
    """最小 JS 对象：属性表 + 可调用标记。`__getattr__` 走属性表。"""

    def __init__(self, props=None, name='Object', callable_=False, ctor=None):
        self.props = dict(props or {})
        self.name = name
        self.callable_ = callable_
        self.ctor = ctor

    def get(self, k):
        if k in self.props:
            return self.props[k]
        return getattr(self, k, None) if hasattr(self, k) and k not in ('props', 'name') else None

    def set(self, k, v):
        self.props[k] = v
        return True

    def __repr__(self):
        return '<JSObj %s %d props>' % (self.name, len(self.props))


class JSArray(list):
    def __init__(self, items=()):
        list.__init__(self, items)
        self.name = 'Array'


class JSFunc(JSObj):
    def __init__(self, fn, name='', props=None):
        JSObj.__init__(self, props, name=name or getattr(fn, '__name__', 'fn'),
                       callable_=True)
        self.fn = fn

    def __call__(self, *a):
        return self.fn(*a)


class Undefined(object):
    _i = None

    def __new__(cls):
        if cls._i is None:
            cls._i = object.__new__(cls)
        return cls._i

    def __repr__(self):
        return 'undefined'

    def __bool__(self):
        return False


UNDEF = Undefined()


class Global(JSObj):
    def __init__(self):
        JSObj.__init__(self, {}, name='globalThis')


GLOBAL = Global()


def _install_builtins():
    """Go `wasm_exec` 的 `js.Value` 机制初始化时会问这几个内建构造器。"""
    def _obj(*a):
        o = JSObj(name='Object')
        if a and isinstance(a[0], dict):
            o.props.update(a[0])
        return o

    def _arr(*a):
        return JSArray(a[0] if (len(a) == 1 and isinstance(a[0], (list, tuple))) else a)

    def _str(*a):
        return '' if not a else str(a[0])

    def _num(*a):
        return 0 if not a else float(a[0])

    def _fn(*a):
        return JSFunc(lambda *x: UNDEF, name='anonymous')

    def _eval(src):
        # Go 侧靠 `eval` 拿载荷源码。纯 Python 里没有 JS 引擎 —— 这里只把源码收下并回报，
        # 供后续用别的手段处理（这正是「补环境」还差的那一步）。
        EVAL_SOURCES.append(src)
        return UNDEF

    def _json_parse(s):
        return json.loads(s)

    def _json_stringify(v):
        return json.dumps(v, ensure_ascii=False)

    def _type_error(*a):
        return JSObj(name='TypeError')

    GLOBAL.props.update({
        'globalThis': GLOBAL,
        'Object': JSFunc(_obj, 'Object'),
        'Array': JSFunc(_arr, 'Array'),
        'String': JSFunc(_str, 'String'),
        'Number': JSFunc(_num, 'Number'),
        'Boolean': JSFunc(lambda *a: bool(a[0]) if a else False, 'Boolean'),
        'Function': JSFunc(_fn, 'Function'),
        'Symbol': JSFunc(lambda *a: JSObj(name='Symbol'), 'Symbol'),
        'Error': JSFunc(_type_error, 'Error'),
        'TypeError': JSFunc(_type_error, 'TypeError'),
        'Date': JSFunc(lambda *a: JSObj(name='Date'), 'Date'),
        'RegExp': JSFunc(lambda *a: JSObj(name='RegExp'), 'RegExp'),
        'Promise': JSFunc(lambda *a: JSObj(name='Promise'), 'Promise'),
        'Math': JSObj({'floor': JSFunc(math.floor, 'floor'),
                       'random': JSFunc(lambda: 0.5, 'random'),
                       'abs': JSFunc(abs, 'abs'),
                       'max': JSFunc(max, 'max'),
                       'min': JSFunc(min, 'min')}, name='Math'),
        'JSON': JSObj({'parse': JSFunc(_json_parse, 'parse'),
                       'stringify': JSFunc(_json_stringify, 'stringify')}, name='JSON'),
        'parseInt': JSFunc(lambda s, r=10: int(str(s).strip() or 0, int(r)), 'parseInt'),
        'parseFloat': JSFunc(lambda s: float(str(s).strip() or 0), 'parseFloat'),
        'isNaN': JSFunc(lambda x: not _isnum(x), 'isNaN'),
        'encodeURIComponent': JSFunc(lambda s: str(s), 'encodeURIComponent'),
        'decodeURIComponent': JSFunc(lambda s: str(s), 'decodeURIComponent'),
        'eval': JSFunc(_eval, 'eval'),
    })


EVAL_SOURCES = []


def _isnum(x):
    try:
        float(x)
        return True
    except Exception:
        return False


_install_builtins()


# ---------------------------------------------------------------- NaN-box
class Host(object):
    def __init__(self, g):
        self.g = g                      # govm_all 模块
        self.vals = [UNDEF, UNDEF, None, True, False, GLOBAL]   # 0..5 保留
        self.ids = {}
        self.gets = []                  # 记录被问到的属性名（量化环境面）
        self.calls = []
        self.misses = []

    # --- 值 <-> NaN-box
    def boxDecode(self, bits):
        b = int(bits)
        hi = (b >> 32) & 0xffffffff
        lo = b & 0xffffffff
        if hi == NANHEAD:
            if lo == 1:
                return UNDEF
            if lo == 2:
                return None
            if lo == 3:
                return True
            if lo == 4:
                return False
            return float(b)
        if NANHEAD < hi <= (NANHEAD | 4):
            return self.vals[lo] if 0 <= lo < len(self.vals) else UNDEF
        return float(b)

    def makeRef(self, v):
        if v is UNDEF:
            return (NANHEAD << 32) | 1
        if v is None:
            return (NANHEAD << 32) | 2
        if v is True:
            return (NANHEAD << 32) | 3
        if v is False:
            return (NANHEAD << 32) | 4
        if isinstance(v, float):
            import struct
            return struct.unpack('<q', struct.pack('<d', v))[0]
        if isinstance(v, int):
            return v
        if v is GLOBAL:
            return (NANHEAD << 32) | 5
        if id(v) in self.ids:
            slot = self.ids[id(v)]
        else:
            slot = len(self.vals)
            self.vals.append(v)
            self.ids[id(v)] = slot
        if isinstance(v, str):
            code = 2
        elif isinstance(v, (JSObj, JSArray, JSFunc, list, dict)):
            code = 4 if isinstance(v, JSFunc) else 1
        else:
            code = 1
        return ((NANHEAD | code) << 32) | slot

    # --- 内存读写
    def storeValue(self, addr, v):
        a = int(addr)
        ref = self.makeRef(v)
        self.g.M32[a] = ref & 0xffffffff
        self.g.M32[a + 4] = (ref >> 32) & 0xffffffff

    def loadValue(self, addr):
        a = int(addr)
        lo = self.g.M32[a]
        hi = self.g.M32[a + 4]
        return self.boxDecode((hi << 32) | lo)

    def loadSliceOfValues(self, addr, ln):
        return [self.loadValue(int(addr) + i * 8) for i in range(int(ln))]

    def loadString(self, addr, ln):
        a, n = int(addr), int(ln)
        return bytes(self.g.MEM.b[a:a + n]).decode('utf-8', 'replace')

    def enc(self, s):
        return str(s).encode('utf-8')

    # --- Reflect
    def rget(self, obj, k):
        if isinstance(obj, (JSObj, Global)):
            return obj.get(k)
        if isinstance(obj, JSArray):
            if k == 'length':
                return len(obj)
            try:
                return obj[int(k)]
            except Exception:
                return UNDEF
        if isinstance(obj, str):
            if k == 'length':
                return len(obj)
            try:
                return obj[int(k)]
            except Exception:
                return UNDEF
        if isinstance(obj, dict):
            return obj.get(k, UNDEF)
        return UNDEF

    def rset(self, obj, k, v):
        if isinstance(obj, (JSObj, Global)):
            obj.set(k, v)
            return True
        if isinstance(obj, JSArray):
            try:
                obj[int(k)] = v
            except Exception:
                pass
            return True
        if isinstance(obj, dict):
            obj[k] = v
            return True
        return False

    def rapply(self, fn, this, args):
        if isinstance(fn, JSFunc):
            try:
                return fn(*args)
            except Exception as e:
                self.misses.append(('apply-err', fn.name, str(e)[:60]))
                return UNDEF
        return UNDEF

    # ------------------------------------------------------------ op 分派
    def copyBytesToGo(self, sp, op, *r):
        A = [sp, op] + list(r)
        a = lambda i: int(A[i] or 0)
        try:
            v7 = self.boxDecode(int(A[2] or 0) + int(A[1] or 0))
        except Exception:
            v7 = UNDEF
        op = int(op)
        self.calls.append(op)

        if op == 1022:                                   # Value.Set
            k = self.loadString(a(4), a(5))
            return 1 if self.rset(v7, k, self.boxDecode(int(A[3] or 0))) else 0

        if op == 1024:                                   # Value.Get
            k = self.loadString(a(4), a(5))
            self.gets.append(k)
            v = self.rget(v7, k)
            if v is UNDEF:
                self.misses.append(('get', k, type(v7).__name__))
            self.storeValue(a(0), v)
            self.g.M8[int(sp) + 8] = 1
            return 0

        if op == 1026:                                   # Value.Call
            name = self.loadString(a(4), a(5))
            args = self.loadSliceOfValues(a(7), a(8))
            self.calls.append(name)
            fn = self.rget(v7, name)
            self.storeValue(a(0), self.rapply(fn, v7, args))
            self.g.M8[int(sp) + 8] = 1
            return 0

        if op == 1028:                                   # Value.String
            b = self.enc(self.to_str(v7))
            self.g.M32[int(sp)] = a(0) if False else int(sp)
            self.storeValue(a(0), b)
            self.g.M32[int(sp) + 8] = len(b)
            return 0

        if op == 1030:                                   # copyBytesToGo(dst, src, len)
            dst, ln = a(10), a(11)
            if isinstance(v7, (bytes, bytearray)):
                src = bytes(v7)
            elif isinstance(v7, str):
                src = v7.encode('utf-8', 'replace')
            elif isinstance(v7, (JSArray, list)):
                src = bytes(int(x) & 0xff for x in v7)
            else:
                src = b''
            n = min(len(src), ln)
            self.g.MEM.b[dst:dst + n] = src[:n]
            return n

        if op == 1032:                                   # Value.Invoke（方法调用）
            args = self.loadSliceOfValues(a(7), a(8))
            name = self.loadString(a(10), a(11)) if a(11) else ''
            fn = self.rget(v7, name) if name else v7
            self.storeValue(a(0), self.rapply(fn, v7, args))
            self.g.M8[int(sp) + 8] = 1
            return 0

        if op == 1034:                                   # Value.New（构造）
            args = self.loadSliceOfValues(a(7), a(8))
            self.storeValue(a(0), JSObj(name='new', props={'args': args}))
            self.g.M8[int(sp) + 8] = 1
            return 0

        self.misses.append(('op', op, tuple(A[:12])))
        return 0

    def to_str(self, v):
        if v is UNDEF:
            return 'undefined'
        if v is None:
            return 'null'
        if v is True:
            return 'true'
        if v is False:
            return 'false'
        if isinstance(v, str):
            return v
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        if isinstance(v, (JSObj, JSArray, JSFunc)):
            return '[object %s]' % getattr(v, 'name', 'Object')
        return str(v)


def install(g):
    h = Host(g)
    g._HOST = h
    g.boxDecode = h.boxDecode
    g.makeRef = h.makeRef
    g.syscall_js_copyBytesToGo = h.copyBytesToGo
    g.syscall_js_valueLength = lambda ref: len(h.boxDecode(ref)) if h.boxDecode(ref) is not None else 0
    g.syscall_js_valueIndex = lambda ref, i: h.makeRef((h.boxDecode(ref) or {})[i])
    g.syscall_js_valueSetIndex = lambda ref, i, v: h.rset(h.boxDecode(ref), i, h.boxDecode(v))
    g.syscall_js_finalizeRef = lambda *a: None
    g.syscall_js_valueNew = lambda dst, ctor, p, n: h.storeValue(dst, JSObj(name='new'))
    g.runtime_ticks = lambda: 0
    g.runtime_fd_write = lambda *a: 0
    return h
