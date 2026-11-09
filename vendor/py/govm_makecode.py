#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""govm_makecode.py —— Go VM（TinyGo→wasm）字节码的**静态反汇编器 / makecode**

它做什么
--------
1. `--extract`  : 从 build wasm 里静态抠出
                  · 程序字节流（10565 词，每词 1 字节，内存里占 4 字节槽）
                  · 常量池原始条目（101 条）
2. `--isa`      : 从反编译产物 `.probe/wt/zse.decompiled.js` 里**机械提取**
                  104 个取指格式（elem 表索引 → 语义派发器 + 操作数字段表达式 + pc 步进）
3. `--decode`   : 用上表线性扫全程序，产出
                  · out/prog.listing.txt  逐条反汇编
                  · out/prog.decompiled.js 可读伪代码
4. `--verify`   : 用真机/pure-VM 轨迹（pc, 语义op）对拍，证明反汇编正确

为什么这样做（而不是重写一个解释器）
------------------------------------
程序字节流是**定长/变长混合的位打包编码**：每个指令的第一个字节是 elem 表索引，
索引选出「用哪几个字、怎么按位移拼出语义 op 与操作数」。这套规则**已经完整地写在
反编译产物里**（104 个 `$bound` 函数体），所以不靠猜，直接把它当规格执行。

用法
----
  python py/govm_makecode.py --extract --wasm .probe/build-859e.wasm --out out/
  python py/govm_makecode.py --isa --out out/
  python py/govm_makecode.py --decode --out out/
  python py/govm_makecode.py --verify --out out/ [--trace .probe/tmp/mine_int.txt]
"""
import os, re, sys, json, base64, struct, argparse, functools

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
P = lambda *a: os.path.join(ROOT, *a)

DECOMPILED = os.environ.get('ZSE_DECOMPILED') or P('.probe', 'wt', 'zse.decompiled.js')
TABLE_JSON = os.environ.get('ZSE_TABLE') or P('.probe', 'zsec', '_table.json')

# `O0o0Oo` **不是**派发器：它是 slice 扩容助手（runtime_alloc + memCopy），
#   放进列表会把 24 个「自派发格式」误标成派发器（实测踩过）。
DISPATCHERS = ['00oooO', 'OOo0o0', 'O0O00o', '0OO0Oo']
HELPERS = ['O0o0Oo']

# ------------------------------------------------------------------ wasm 解析


def _uleb(w, p):
    r = 0; s = 0
    while True:
        x = w[p]; p += 1
        r |= (x & 0x7f) << s; s += 7
        if not (x & 0x80):
            return r, p


def wasm_sections(w):
    p = 8
    while p < len(w):
        sid = w[p]; p += 1
        sz, p = _uleb(w, p)
        yield sid, w[p:p + sz]
        p += sz


def extract_program(wasm_path):
    """返回 (program_bytes, blob, pool_raw)。

    已实证（本会话）: blob 头部 `00 00 <count:u16be>`，program = blob[4 : 4+count]，
    逐字节与真机内存词的低字节 10565/10565 相同。
    """
    w = open(wasm_path, 'rb').read()
    data = [s for i, s in wasm_sections(w) if i == 11]
    blob = None
    for sec in data:
        for m in re.finditer(rb'[A-Za-z0-9+/=]{1000,}', sec):
            try:
                d = base64.b64decode(m.group())
            except Exception:
                continue
            if len(d) > 5000:
                blob = d
                break
        if blob:
            break
    if not blob:
        raise SystemExit('未找到内嵌 base64 blob')
    count = int.from_bytes(blob[2:4], 'big')
    prog = blob[4:4 + count]
    tail = blob[4 + count:]
    pool = []
    i = 0
    while i < len(tail) - 2:
        if tail[i] == 0 and 1 <= tail[i + 1] <= 250 and i + 2 + tail[i + 1] <= len(tail):
            pool.append(tail[i + 2:i + 2 + tail[i + 1]])
            i += 2 + tail[i + 1]
        else:
            i += 1
    return prog, blob, pool


# ------------------------------------------------------------------ 假内存

class Mem(object):
    """字节内存 = 「只读程序窗口」+「稀疏写覆盖」。

    程序区（`prog_ptr .. prog_ptr+4*n`）是只读的，且**每次 simulate 都相同** ⇒
    预先烘成一份 `bytes`，读走切片；只有 VM 自己写的少数地址进 dict。
    这样避免了「每次 simulate 重灌 10565 词」——那是这条链最大的开销。
    """

    __slots__ = ('d', 'lo', 'hi', 'buf', 'dirty')

    def __init__(self, lo=0, hi=0, buf=b''):
        self.d = {}
        self.lo = lo; self.hi = hi; self.buf = buf
        self.dirty = False          # 程序窗口是否被写过（写过就退回逐字节路径）

    def get(self, a):
        v = self.d.get(a)
        if v is not None:
            return v
        if self.lo <= a < self.hi:
            return self.buf[a - self.lo]
        return 0

    def set(self, a, v):
        self.d[a] = v & 0xff
        if self.lo <= a < self.hi:
            self.dirty = True

    def read(self, a, n):
        """小端读 n 字节。"""
        if not self.dirty and self.lo <= a and a + n <= self.hi:
            o = a - self.lo
            return int.from_bytes(self.buf[o:o + n], 'little')
        v = 0
        for i in range(n - 1, -1, -1):
            v = (v << 8) | self.get(a + i)
        return v

    def write(self, a, n, v):
        for i in range(n):
            self.d[a + i] = (v >> (8 * i)) & 0xff
        if self.lo <= a + n - 1 and a < self.hi:
            self.dirty = True


class View(object):
    """M32/M8/M64 都是**字节寻址**视图（与反编译产物里的用法一致）：
       `M32[byte_addr]` 读该地址起 4 字节小端。"""
    __slots__ = ('m', 'scale', 'mask')

    def __init__(self, m, scale):
        self.m = m; self.scale = scale
        self.mask = (1 << (8 * scale)) - 1

    def __getitem__(self, k):
        return self.m.read(int(k), self.scale)

    def __setitem__(self, k, v):
        self.m.write(int(k), self.scale, int(v) & self.mask)


# 程序窗口的预烘缓存：同一份 prog 只烘一次
_PROGBUF = {}


def prog_window(prog, prog_ptr):
    """把 `prog`（每词 1 字节，内存里占 4 字节槽）烘成只读 bytes，按 prog 身份缓存。"""
    key = id(prog)
    b = _PROGBUF.get(key)
    if b is None or _PROGBUF.get(('len', key)) != len(prog):
        ba = bytearray(len(prog) << 2)
        for i, x in enumerate(prog):
            ba[i << 2] = x
        b = bytes(ba)
        _PROGBUF[key] = b
        _PROGBUF[('len', key)] = len(prog)
    return prog_ptr, prog_ptr + len(b), b


# ------------------------------------------------------------------ 表达式求值

_IDENT = re.compile(r'\b(l\d+|_h\d+|p0|sp)\b')
_NUM = re.compile(r'\b(\d+)n\b')


@functools.lru_cache(maxsize=None)
def _prep(e):
    e = _NUM.sub(r'\1', e)
    e = e.replace('>>>', '>>')
    e = e.replace('!==', '!=')
    return e


_EVCODE = {}


def _evcode(expr):
    c = _EVCODE.get(expr)
    if c is None:
        c = compile(_prep(expr), '<ir>', 'eval')
        _EVCODE[expr] = c
    return c


def ev(expr, S):
    return eval(_evcode(expr), {'__builtins__': {}}, S)


# ------------------------------------------------------------------ 取指格式提取

_SKIP = ('}', 'break ', '$B', 'runtime_', 'throw ', 'return', 'callIndirect(', 'let ',
         'if (', 'while ', 'try{', 'catch')
def decode_pool_entry(data, skip=0, k0=0x30, seed2=0x22, xor=0x06):
    """blob 常量池条目的解码。

    条目格式 = `<密文>`，密文长度 == len(明文)，明文是 Go 源码里的**字符串字面量**
    （含首尾引号，如 `"Go"` / `"005_…"` / 字母表）。密钥递推：

        p[j] = c[j] ^ K[j]
        K[0] = k0
        K[j] = K[j-1] ^ p[j-1] ^ p[j-2]      (j >= 1，且 p[-1] := seed2)

    再整体异或 `xor` 才是明文：`plain[j] = p[j] ^ xor`。

    `(k0, xor)` **每 build 不同**，但可由第 0 条（恒为 `"Go"`）唯一解出 —— 见
    `pool_key()`。859e 的 k0=0x30/xor=0x06 只是恰好。

      ⚠️ `skip=1, xor=0` 只能解对 63/101 条，且会把 `rk`（#66..#97）与字母表（#98）解成乱码。
      正确式必须带 `xor`；`rk` / 字母表就是池里的**静态字符串字面量**。
    """
    out = bytearray(); k = k0; pm1 = seed2
    for x in data[skip:]:
        p = x ^ k
        out.append(p ^ xor)
        k = (k ^ p ^ pm1) & 255
        pm1 = p
    return bytes(out)


GO_LITERAL = b'"Go"'          # 池第 0 条恒为它，用来解 (k0, xor)


def pool_key(pool, target=GO_LITERAL):
    """由池第 0 条解出该 build 的 (k0, xor)；解不出返回 None。"""
    for k0 in range(256):
        d = decode_pool_entry(pool[0], skip=0, k0=k0, xor=0)
        x = d[0] ^ target[0]
        if bytes(c ^ x for c in d) == target:
            return k0, x
    return None


def decode_pool(pool):
    """整池解码 → (k0, xor, [明文 bytes, ...])；解不出返回 (None, None, None)。"""
    key = pool_key(pool)
    if not key:
        return None, None, None
    k0, xor = key
    return k0, xor, [decode_pool_entry(e, skip=0, k0=k0, xor=xor) for e in pool]


# 每 build 的两个关键常量就在池里：
#   · 32 条纯十进制串（连续） = rk[i] ^ 0x7B7B7B7B
#   · 1 条 `"<64 字符排列>"`   = radix64 字母表
CHARSET = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/=')
RK_XOR = 0x7B7B7B7B


def static_profile(wasm_path):
    """从 wasm 静态抠出每 build 的 (rk, alphabet)，**零执行**。

    返回 dict(k0, xor, rk[32], alphabet, idx_rk, idx_alphabet)；失败返回 None。
    """
    prog, blob, pool = extract_program(wasm_path)
    k0, xor, plain = decode_pool(pool)
    if plain is None:
        return None
    txt = [p.decode('utf-8', 'replace') for p in plain]

    # 字母表：`"<64 字符>"`，64 个互异字符且落在 base64 字符集（+ 换成 =）。
    # ⚠️ 池里有**两个**这样的条目：#11 是**跨 build 恒定**的默认表，最后一个（859e 是 #98）
    #    才是本 build 的 radix64 字母表（已用 profile 的逐字符自证确认）。
    cands = []
    for i, s in enumerate(txt):
        if len(s) == 66 and s[0] == '"' and s[-1] == '"':
            body = s[1:-1]
            if len(set(body)) == 64 and set(body) == CHARSET:
                cands.append(i)
    idx_al = cands[-1] if cands else None
    # rk：32 条连续纯十进制
    idx_rk = None
    run = 0
    for i, s in enumerate(txt):
        if s.isdigit() and 5 <= len(s) <= 10:
            run += 1
            if run == 32:
                idx_rk = i - 31
                break
        else:
            run = 0
    if idx_al is None or idx_rk is None:
        return None
    rk = [int(txt[idx_rk + k]) ^ RK_XOR for k in range(32)]
    return dict(k0=k0, xor=xor, rk=rk, alphabet=txt[idx_al][1:-1],
                idx_rk=idx_rk, idx_alphabet=idx_al)


def elem_of(b):
    """程序字节 → elem 索引（与真机 `_00oooO` 的 op 完全一致）。
    运行时分派表 `table[b].f1` 是 `[1, 104, 103, ..., 2]`：
    除 b=0 落在 1 之外，其余都是 `105 - b`。"""
    return 1 if b == 0 else 105 - b


# 程序字读取（4 字节步长）的形态
_PROGREAD = re.compile(r'^(M32|M16)\[.*<<\s*2.*\]$')


def extract_isa():
    src = open(DECOMPILED, encoding='utf8').read()
    fns = {}
    for m in re.finditer(r'^function ([A-Za-z0-9_$]+)\(', src, re.M):
        nm = m.group(1)
        i = src.index('{', m.end() - 1)
        d = 0
        for j in range(i, len(src)):
            if src[j] == '{':
                d += 1
            elif src[j] == '}':
                d -= 1
                if d == 0:
                    break
        fns[nm] = src[m.start():j + 1]
    tbl = json.load(open(TABLE_JSON, encoding='utf8'))
    isa = {}
    for k, v in tbl.items():
        # 键就是**程序字节值**（实测：按这个映射线性扫描的步进恒为 4，对齐自洽）。
        #   键 0 缺失、105..107 是 3 个非 $bound 辅助函数 —— 保留原样，查不到就记「无格式」。
        idx = int(k)
        body = fns.get(v)
        if body is None:
            continue
        lines = [l.strip() for l in body.split('\n')]
        call = None; callargs = None
        for l in lines:
            for d in DISPATCHERS:
                pre = '__main__0oOoOO___' + d + '('
                if l.startswith(pre):
                    call = d
                    inner = l[len(pre):l.rstrip().rstrip(';').rfind(')')]
                    callargs = inner
                    break
            if call:
                break
        isa[idx] = dict(name=v, dispatcher=call, args=callargs, nlines=len(lines),
                        # 自派发格式：体内自带尾部取指（`M32[p0 + 376]` + `callIndirect`），
                        #   它自己就是下一个取指点，不再依赖派发器的 `pc += 1`。
                        selfdisp=('callIndirect(' in body or 'M32[p0 + 376]' in body))
    return fns, isa


_RE_MEMWR = re.compile(r'^(M32|M8|M64)\[([^\]]+)\]\s*=\s*(.+);$')
_RE_ASSIGN = re.compile(r'^([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(.+);$')

_BODY_CACHE = {}


def compile_body(body):
    """把 `$bound` 的函数体预编译成算子列表（按 body 文本缓存）。

    `simulate()` 会被调 4 万多次，而不同 body 只有一百来个 —— 逐行 `strip()` + 两次
    `re.match` 的解析只该做一次。
    """
    ops = _BODY_CACHE.get(body)
    if ops is not None:
        return ops
    ops = []
    for raw in body.split('\n'):
        l = raw.strip()
        if not l or l.startswith(_SKIP):
            continue
        if l.startswith('M32[p0 + 20] ='):
            # pc 字段写回（不是 p0！把这条写成 p0=... 会让后面全部读到 0）
            ops.append(('pc', l[len('M32[p0 + 20] ='):].rstrip(';')))
            continue
        hit = None
        for d in DISPATCHERS:
            pre = '__main__0oOoOO___' + d + '('
            if l.startswith(pre):
                inner = l[len(pre):]
                inner = inner[:inner.rstrip().rstrip(';').rfind(')')]
                hit = ('call', d, _split_args(inner)[1:])
                break
        if hit:
            ops.append(hit)
            continue
        m = _RE_MEMWR.match(l)
        if m:
            ops.append(('sub', m.group(2)))
            continue
        m = _RE_ASSIGN.match(l)
        if m:
            ops.append(('assign', m.group(1), m.group(2),
                        bool(_PROGREAD.match(m.group(2)))))
            continue
    _BODY_CACHE[body] = ops
    return ops


def simulate(body, isa_rec, pc, prog, vm=0x100000, prog_ptr=0x400000, ntab=108):
    """跑一个 $bound 取指器（只执行到派发器调用为止）。返回 (dispatcher, args, nextPc)。"""
    mem = Mem(*prog_window(prog, prog_ptr))
    M8, M32, M64 = View(mem, 1), View(mem, 4), View(mem, 8)
    # 记录所有落在程序区间的 4 字节读 —— 这就是这条指令的**操作数字段**（通用，不依赖正则）
    _reads = []
    # 多字节字段必须走视图写（直接 mem.set 只写 1 字节，读回来就是 0）
    M32[vm + 20] = pc
    M32[vm + 308] = prog_ptr
    M32[vm + 312] = len(prog)
    M32[vm + 376] = 0x500000
    M32[vm + 380] = ntab
    S = {'M8': M8, 'M16': M32, 'M32': M32, 'M64': M64,
         'uge': lambda a, b: (a & 0xffffffff) >= (b & 0xffffffff),
         'ugt': lambda a, b: (a & 0xffffffff) > (b & 0xffffffff),
         'ule': lambda a, b: (a & 0xffffffff) <= (b & 0xffffffff),
         'ult': lambda a, b: (a & 0xffffffff) < (b & 0xffffffff),
         'irem': lambda a, b: 0 if b == 0 else a % b,
         'p0': vm, 'sp': 0}
    hit = None
    for op in compile_body(body):
        k = op[0]
        if k == 'pc':
            try:
                M32[S['p0'] + 20] = int(ev(op[1], S))
            except Exception:
                pass
        elif k == 'call':
            args = []
            for a in op[2]:
                try:
                    args.append(int(ev(a, S)))
                except Exception:
                    args.append(None)
            hit = (op[1], args)
            break
        elif k == 'sub':
            try:
                S['p0'] = S['p0']
                ev(op[1], S)
            except Exception:
                pass
        elif k == 'assign':
            try:
                v = ev(op[2], S)
            except Exception:
                v = 0
            S[op[1]] = v
            # 程序字读取的通用识别：4 字节步长（`<< 2`）的 M32 下标就是程序/操作数字段。
            # 寄存器是 8 字节步长（`<< 3`），不会误伤。
            if op[3]:
                _reads.append(int(v))
    nextpc = M32[vm + 20]
    return (hit[0] if hit else None), (hit[1] if hit else None), nextpc, _reads


def _split_args(s):
    out = []; d = 0; cur = ''
    for ch in s:
        if ch in '([':
            d += 1
        elif ch in ')]':
            d -= 1
        if ch == ',' and d == 0:
            out.append(cur); cur = ''
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out]


# ------------------------------------------------------------------ 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pool', action='store_true', help='解 blob 常量池 -> out/pool_decoded.json')
    ap.add_argument('--static-profile', action='store_true',
                    help='零执行地从 wasm 抠出每 build 的 rk/alphabet -> out/static_profile.json')
    ap.add_argument('--extract', action='store_true')
    ap.add_argument('--isa', action='store_true')
    ap.add_argument('--decode', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--len-from-trace', dest='lentrace', action='store_true',
                    help='从真机绑定循环轨迹 out/../.probe/tmp/mine_bind.txt 实测每个 elem 的指令长度')
    ap.add_argument('--listing-from-trace', dest='ltrace', action='store_true',
                    help='用真机绑定循环轨迹产出权威 listing（out/prog.listing.trace.txt）')
    ap.add_argument('--trace-dump', dest='tdump', action='store_true',
                    help='按真机取指轨迹产出**已验证**的反汇编（out/prog.trace.listing.txt）')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--wasm', default=P('.probe', 'build-859e.wasm'))
    ap.add_argument('--out', default=P('out'))
    ap.add_argument('--trace', default=P('.probe', 'tmp', 'mine_tail5.txt'))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    if a.static_profile:
        sp = static_profile(a.wasm)
        if sp is None:
            raise SystemExit('[static-profile] 解不出（池第 0 条不是 %r 或结构不符）' % GO_LITERAL)
        out = os.path.join(a.out, 'static_profile.json')
        json.dump(sp, open(out, 'w', encoding='utf8'), ensure_ascii=False, indent=1)
        print('[static-profile] wasm=%s' % os.path.basename(a.wasm))
        print('  k0=%#04x xor=%#04x  rk@#%d alphabet@#%d' % (sp['k0'], sp['xor'], sp['idx_rk'], sp['idx_alphabet']))
        print('  rk[0..3] = %s' % [hex(x) for x in sp['rk'][:4]])
        print('  alphabet = %s' % sp['alphabet'])
        print('  -> %s' % out)
        return

    if a.extract or a.all:
        prog, blob, pool = extract_program(a.wasm)
        open(os.path.join(a.out, 'prog.bin'), 'wb').write(prog)
        json.dump([list(x) for x in pool], open(os.path.join(a.out, 'pool_raw.json'), 'w'))
        print('[extract] program=%d 词  blob=%d 池=%d 条 -> %s' %
              (len(prog), len(blob), len(pool), a.out))
    prog_path = os.path.join(a.out, 'prog.bin')
    if not os.path.exists(prog_path):
        raise SystemExit('先跑 --extract')
    prog = open(prog_path, 'rb').read()

    if a.pool:
        _, _, pool = extract_program(a.wasm)
        k0, xor, plain = decode_pool(pool)
        if plain is None:
            raise SystemExit('[pool] 解不出 (k0, xor)：池第 0 条不是 %r' % GO_LITERAL)
        got = [p.decode('utf-8', 'replace') for p in plain]
        json.dump({'k0': k0, 'xor': xor, 'entries': got},
                  open(os.path.join(a.out, 'pool_decoded.json'), 'w',
                       encoding='utf8'), ensure_ascii=False, indent=1)
        ok = sum(1 for x in got if all(32 <= ord(ch) < 127 or ord(ch) > 0x2000 for ch in x))
        print('[pool] k0=%#04x xor=%#04x  %d 条 -> out/pool_decoded.json（%d 条可打印/中文）'
              % (k0, xor, len(got), ok))
        for i in (0, 1, 2, 98, 100):
            print('        #%-3d %r' % (i, got[i][:60]))
        return

    if a.isa or a.all:
        fns, isa = extract_isa()
        # 用真实程序把每个格式跑一遍，拿到 pc 步进与操作数
        built = {}
        for idx, rec in sorted(isa.items()):
            body = fns.get(rec['name'])
            if not body:
                continue
            pc = 100
            d, args, npc, words = simulate(body, rec, pc, prog)
            # expr = 原始操作数字段表达式（规格）；sample = 在 pc=100 求出的样例值（仅示意）
            built[idx] = dict(name=rec['name'], dispatcher=d, adv=npc - pc,
                              expr=rec['args'], sample=args,
                              selfdisp=rec.get('selfdisp', False))
        json.dump(built, open(os.path.join(a.out, 'isa.json'), 'w'), indent=1)
        dd = {}
        for v in built.values():
            dd[v['dispatcher']] = dd.get(v['dispatcher'], 0) + 1
        print('[isa] %d 个取指格式；派发器分布 %s' % (len(built), dd))

    isa_path = os.path.join(a.out, 'isa.json')
    if not os.path.exists(isa_path):
        raise SystemExit('先跑 --isa')
    isa = {int(k): v for k, v in json.load(open(isa_path)).items()}
    fns, _ = extract_isa()
    # 实测指令长度表（elem -> 字节数 4/8/10），来自真机绑定循环轨迹
    _lp = os.path.join(a.out, 'len_emp.json')
    LEN = {int(k): v for k, v in json.load(open(_lp)).items()} if os.path.exists(_lp) else {}

    if a.decode or a.all:
        pc = 0; lines = []; unknown = 0
        while pc < len(prog):
              # 程序字节 b 选中的是 elem[105 - b]（实测 8196/8196 成立），不是 elem[b]。
            idx = prog[pc]
            rec = isa.get(elem_of(idx))
            if not rec:
                lines.append('%6d  %-4s  <idx=%d 无格式/未派发>' % (pc, '', idx))
                pc += 1; unknown += 1
                continue
            ln = LEN.get(elem_of(idx))
            d, args, npc, words = simulate(fns[rec['name']], rec, pc, prog)
            who = rec['dispatcher'] or rec['name'].replace('__main__0oOoOO___', '')
            lines.append('%6d  %-4d elem=%-4d %-22s op=%-6s words=%s  len=%s' %
                         (pc, idx, elem_of(idx), who, (args[0] if args else None), words,
                          ln if ln else '?'))
            # 步进 = **实测指令长度**（4 / 8 / 10 字节），来自真机绑定循环轨迹。
            #   模型推导出来的 adv 只能覆盖一部分（自派发格式的 pc 语义不同），实测长度才是准的。
            pc += ln if ln else 4
        open(os.path.join(a.out, 'prog.listing.txt'), 'w', encoding='utf8').write('\n'.join(lines))
        # 可读伪代码（与 listing 同源，换成更接近源码的写法）
        js = ['// 由 py/govm_makecode.py 从 build-859e.wasm 静态反汇编（未执行任何 wasm）',
              '// 程序 %d 词；取指格式 %d 个' % (len(prog), len(isa))]
        for ln in lines:
            js.append('// ' + ln)
        open(os.path.join(a.out, 'prog.decompiled.js'), 'w', encoding='utf8').write('\n'.join(js))
        print('[decode] %d 条指令（%d 处无法识别）-> %s' %
              (len(lines), unknown, os.path.join(a.out, 'prog.listing.txt')))

    if a.lentrace:
        import collections as _c
        src = P('.probe', 'tmp', 'mine_bind.txt')
        rr = []
        for ln in open(src, encoding='utf8', errors='replace'):
            m = re.match(r'seq=(\d+) BIND pc=(\d+) elem=(\d+)', ln.strip())
            if m:
                rr.append((int(m.group(2)), int(m.group(3))))
        L = _c.defaultdict(_c.Counter)
        for k in range(len(rr) - 1):
            d = rr[k + 1][0] - rr[k][0]
            if 0 < d <= 64:
                L[rr[k][1]][d] += 1
        out = {str(e): c.most_common(1)[0][0] for e, c in L.items()}
        json.dump(out, open(os.path.join(a.out, 'len_emp.json'), 'w'), indent=1)
        amb = sum(1 for e, c in L.items() if len(c) > 1)
        print('[len] %d 个 elem 的长度已实测（4/8/10），其中 %d 个非唯一 -> out/len_emp.json'
              % (len(out), amb))

    if a.ltrace:
        # 轨迹驱动 listing：真机绑定循环给出**权威的指令起始 pc 序列**，
        #   比任何静态线性扫都准（静态扫遇到变长/分支会失步）。
        seen = {}
        for ln in open(P('.probe', 'tmp', 'mine_bind.txt'), encoding='utf8', errors='replace'):
            m = re.match(r'seq=(\d+) BIND pc=(\d+) elem=(\d+)', ln.strip())
            if m:
                pc0 = int(m.group(2))
                if pc0 not in seen:
                    seen[pc0] = int(m.group(3))
        rows = []
        for pc0 in sorted(seen):
            idx = prog[pc0]; e = seen[pc0]
            r = isa.get(e)
            if not r:
                rows.append('%6d  %-4d elem=%-4d <无格式>' % (pc0, idx, e)); continue
            d, args, npc, words = simulate(fns[r['name']], r, pc0, prog)
            who = r['dispatcher'] or r['name'].replace('__main__0oOoOO___', '')
            rows.append('%6d  %-4d elem=%-4d %-22s op=%-6s words=%s' %
                        (pc0, idx, e, who, (args[0] if args else None), words))
        open(os.path.join(a.out, 'prog.listing.trace.txt'), 'w', encoding='utf8').write(chr(10).join(rows))
        print('[listing-trace] %d 条指令（真机绑定循环起始 pc）-> %s'
              % (len(rows), os.path.join(a.out, 'prog.listing.trace.txt')))

    if a.tdump:
        # 轨迹驱动反汇编：静态线性扫遇到分支/变长操作数会失步（实测步进是 8/20/28/44…），
        #   所以**可信的**反汇编必须以真机取指轨迹为准。这里逐条给出：
        #   取指 pc / 程序字节 / elem / 该格式的解码结果 / 下一条的执行者（实测）。
        pat = re.compile(r'(?:seq=(\d+) )?(\S+) pc=(\d+) idx=(\d+)')
        items = []
        for fn in [P('.probe', 'tmp', 'mine_fetch.txt'), P('.probe', 'tmp', 'mine_tail5.txt')]:
            if not os.path.isfile(fn):
                continue
            for ln in open(fn, encoding='utf8', errors='replace'):
                m = pat.match(ln.strip())
                if m:
                    items.append((int(m.group(1)) if m.group(1) else 0,
                                  m.group(2), int(m.group(3)), int(m.group(4))))
        items.sort(key=lambda x: x[0])
        out = ['# 轨迹驱动反汇编（ground truth）：共 %d 次取指' % len(items),
               '# 列: seq 取指pc 字节 elem 格式/派发器 op 操作数 步进 下一条执行者']
        for i, (sq, tag, pc, idx) in enumerate(items):
            f = elem_of(idx)
            r = isa.get(f)
            if not r:
                out.append('%6d %6d %4d %4d  <无格式>' % (sq, pc, idx, f))
                continue
            d, args, npc, words = simulate(fns[r['name']], r, pc, prog)
            nxt = items[i + 1][1] if i + 1 < len(items) else '-'
            nm = r['name'].replace('__main__0oOoOO___', '')
            who = nm if r.get('selfdisp') else (r['dispatcher'] or nm)
            out.append('%6d %6d %4d %4d  %-16s op=%-6s words=%s  步进=%-4s 下一条=%s'
                       % (sq, pc, idx, f, who, (args[0] if args else None), words,
                          (items[i + 1][2] - pc) if i + 1 < len(items) else '-', nxt))
        open(os.path.join(a.out, 'prog.trace.listing.txt'), 'w', encoding='utf8').write(chr(10).join(out))

    if a.verify:
        # 用**完整取指轨迹**验收取指层：轨迹里每条记录给出 (执行者, 取指 pc, idx)。
        #   模型: elem = elem_of(byte)；步进 = adv + (0 if 自派发 else 1)。
        #   要求：从 pc_k 按模型走，**必须精确落在 pc_{k+1}**。
        import glob as _glob
        pat = re.compile(r'(?:seq=(\d+) )?(\S+) pc=(\d+) idx=(\d+)')
        items = []
        _fs = [P('.probe', 'tmp', 'mine_fetch.txt'), P('.probe', 'tmp', 'mine_tail5.txt')]
        for fn in ([x for x in _fs if os.path.isfile(x)] or [a.trace]):
            for ln in open(fn, encoding='utf8', errors='replace'):
                m = pat.match(ln.strip())
                if m:
                    items.append((int(m.group(1)) if m.group(1) else None,
                                  m.group(2), int(m.group(3)), int(m.group(4))))
        if not items:
            print('[verify] 轨迹为空')
        else:
            if all(x[0] is not None for x in items):
                items.sort(key=lambda x: x[0])
            recs = [(t, pc, idx) for _, t, pc, idx in items]
            step = {}
            for f, v in isa.items():
                step[f] = max(1, v['adv']) + (0 if v.get('selfdisp') else 1)
            hit = miss = 0
            bad = []
            for k in range(len(recs) - 1):
                want = recs[k + 1][1]
                pc = recs[k][1]
                for _ in range(64):
                    if pc == want or pc >= len(prog):
                        break
                    f = elem_of(prog[pc])
                    if f not in step:
                        pc = -1
                        break
                    pc += step[f]
                if pc == want:
                    hit += 1
                else:
                    miss += 1
                    if len(bad) < 6:
                        bad.append((recs[k][1], recs[k][2], want, pc))
            print('[verify] 取指链精确落到下一条: %d / %d = %.4f'
                  % (hit, hit + miss, hit / max(1, hit + miss)))
            for b in bad:
                print('   from pc=%d idx=%d 期望 %d，走到 %s' % b)


if __name__ == '__main__':
    main()
