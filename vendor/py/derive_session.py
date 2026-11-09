#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""derive_session.py —— **零执行**从 build wasm 推出会话常量 (rk, alphabet, xreg, mask, iv)

它解决了什么
------------
`ck = '005_' + radix64(encrypt_96(record93 + PKCS#7(3), rk, iv, mask), alphabet, xreg)`
里的五个「每 build 常量」原先只能靠**跑一次驱动读内存**或**观测一对 (record93, ck)** 抠出来。
现在前四个可以**纯静态**推出，第五个（iv）也能推出（4 个 hex 字符的歧义靠少量样本令牌消解）。

原理
----
池里除了 `rk`(#66..#97) 与字母表(#98)，还有 **4 条 `005_…` 占位令牌**。用本 build 的
`rk`/`alphabet` 去解它们，会发现它们是用**同一个** `(iv, mask, alphabet, xreg)` 加密
**同一条占位记录**得到的：

    pt = <w0:4hex> + '0'*76 + 'i' + 'ffffffff' + <tail4> + 0x03*3        (93+3 = 96 字节)

于是：

    mask      = (D(ct_b) ^ ct_{b-1}) ^ 0x30        （b=1..4，因为 pt_b 恒为 '0'×16）
    iv[4:16]  = D(ct_0)[4:16] ^ 0x30 ^ mask        （pt_0[4:16] 恒为 '0'×12）
    xreg      = 使上述两条对 ≥4 条占位令牌**同时成立**的唯一值
    iv[0:4]   = 需要 pt_0[0:4]（= 占位记录的随机 w0）是 4 个 hex 字符；
                该约束对每条令牌给出一个候选集，取交集。

`iv[0:4]` 若仍有多个候选，再用若干**样本令牌**（同一 build 的任意 `005_…`）收敛：
对每条真令牌，`record93` 的位图/指纹/crc 字段都必须是 hex、第 81 字符必须是 `'z'`，
这既是「它属于本 build」的判据，也再给出一条 `iv[0:4]` 约束。

用法
----
    python py/derive_session.py .probe/build-859e.wasm
    python py/derive_session.py <wasm> --tokens tokens.txt -o profile.json
    python py/derive_session.py <wasm> --tokens tokens.txt --verify <参考>.json
"""
import argparse
import glob
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
P = lambda *a: os.path.join(ROOT, *a)
HEX = set('0123456789abcdef')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _cipher():
    for c in (P('py', 'zse_cipher.py'), P('.probe', 'vmsvc', 'zse_cipher.py')):
        if os.path.exists(c):
            return _load('zc', c)
    raise SystemExit('找不到 zse_cipher.py')


def collect_tokens(extra_files=()):
    """工作区里所有 `005_` + 128 字符的令牌（外加显式给的文件）。"""
    import re
    pat = re.compile(rb'005_[A-Za-z0-9/+=]{128}')
    out = set()
    seen_files = set()
    for f in extra_files:
        seen_files.add(os.path.abspath(f))
    for root, dirs, files in os.walk(ROOT):
        if 'node_modules' in root or os.sep + '.git' in root:
            continue
        for fn in files:
            p = os.path.join(root, fn)
            seen_files.add(os.path.abspath(p))
    for p in seen_files:
        try:
            if os.path.getsize(p) > 60 * 1024 * 1024:
                continue
            b = open(p, 'rb').read()
        except Exception:
            continue
        for m in pat.finditer(b):
            out.add(m.group().decode())
    return sorted(out)


def derive(wasm, tokens=(), verbose=True):
    """返回 dict(rk, alphabet, xreg, mask, iv, n_placeholder, n_real, n_iv_cand) 或 None。"""
    mk = _load('mk', P('py', 'govm_makecode.py'))
    zc = _cipher()
    sp = mk.static_profile(wasm)
    if not sp:
        return None
    prog, blob, pool = mk.extract_program(wasm)
    k0, xor, plain = mk.decode_pool(pool)
    txt = [p.decode('utf-8', 'replace') for p in plain]
    rk, al = sp['rk'], sp['alphabet']

    cand = [t[1:][4:] for t in txt if t.startswith('"005_') and len(t) > 100]
    for ck in tokens:
        b = ck[4:]
        if len(b) == 128 and b not in cand:
            cand.append(b)

    for xr in range(256):
        ph, mask = [], None
        for fb in cand:
            try:
                ct = zc.radix64_decode(fb, al, xr)
            except Exception:
                continue
            if len(ct) != 96:
                continue
            ms, ok = set(), True
            for k in range(1, 5):
                d = zc.decrypt_block(ct[16 * k:16 * k + 16], rk)
                x = bytes((q ^ r) ^ 0x30 for q, r in zip(d, ct[16 * (k - 1):16 * k]))
                if len(set(x)) != 1:
                    ok = False
                    break
                ms.add(x[0])
            if not ok or len(ms) != 1:
                continue
            mv = ms.pop()
            if mask is None:
                mask = mv
            if mv == mask:
                ph.append(ct)
        if len(ph) < 4:
            continue
        tails = [bytes(q ^ 0x30 ^ mask for q in zc.decrypt_block(c[:16], rk)[4:16]) for c in ph]
        if len(set(tails)) != 1:
            continue
        tail = tails[0]

        def tag_hex(iv0, cts):
            for c in cts:
                d0 = zc.decrypt_block(c[:16], rk)
                if not all(chr(t) in HEX for t in bytes((a ^ b) ^ mask for a, b in zip(d0[:4], iv0))):
                    return False
            return True

        sols = [('%04x' % v).encode() for v in range(65536) if tag_hex(('%04x' % v).encode(), ph)]

        # 真令牌：结构合法（位图/指纹/crc 全 hex、第 81 字符 = 'z'、末尾 030303）
        real = []
        for ck in tokens:
            b = ck[4:]
            if len(b) != 128:
                continue
            try:
                ct = zc.radix64_decode(b, al, xr)
            except Exception:
                continue
            if len(ct) != 96:
                continue
            q = b''
            for k in range(1, 6):
                d = zc.decrypt_block(ct[16 * k:16 * k + 16], rk)
                q += bytes(a ^ b2 ^ mask for a, b2 in zip(d, ct[16 * (k - 1):16 * k]))
            if not all(chr(t) in HEX for t in q[0:44]):
                continue
            if not all(chr(t) in HEX for t in q[32:64]):
                continue
            if q[64] != ord('z'):
                continue
            if not all(chr(t) in HEX for t in q[65:77]):
                continue
            if q[77:80] != b'\x03\x03\x03':
                continue
            real.append(ct)
        # 只保留**位图相同**的最大簇：工作区里混着别的 build 的令牌，用错字母表解出来
        #   偶尔也能过「结构合法」检查 ⇒ 会加进假约束、把真值排除掉。
        #   位图 w1..w11 是环境相关且跨会话稳定的，用它聚类最可靠。
        import collections as _C
        def _bmap(c):
            q = b''
            for k in range(1, 5):
                d = zc.decrypt_block(c[16 * k:16 * k + 16], rk)
                q += bytes(a ^ b2 ^ mask for a, b2 in zip(d, c[16 * (k - 1):16 * k]))
            return q[0:44]
        _g = _C.Counter(_bmap(c) for c in real)
        if _g:
            _keep = _g.most_common(1)[0][0]
            real = [c for c in real if _bmap(c) == _keep]
        for c in real:
            d0 = zc.decrypt_block(c[:16], rk)
            sols = [iv0 for iv0 in sols
                    if all(chr(t) in HEX for t in bytes((a ^ b) ^ mask for a, b in zip(d0[:4], iv0)))]

        # `iv[0:4]` 完全自由：`iv` 只进第 0 块（P_0 = D(C_0) ^ iv ^ mask），块 1..5 用 C_{b-1}，
        #   与 iv 无关；而 P_0[0:4] 就是位图的 w0（会话随机标签），服务端从第 4 字节起校验。
        #   实测同一 build 换 iv[0:4]（候选集内 3 个 + 集外 0000/ffff）**全部** 302 → 真实页，
        #   而改 iv[4:16]（会改变位图）立刻 403。⇒ 取候选集第一个即可。
        #   若要钉死真值：iv* = D(C_0) ^ record93[0:16] ^ mask（用真机 ck + 真机内存那条 record93）。
        ivs = [s.decode() + tail.decode() for s in sols]
        return dict(rk=rk, alphabet=al, xreg=xr, mask=mask,
                    iv=ivs[0] if ivs else None,
                    iv_unique=(len(ivs) == 1),
                    iv_candidates=ivs,
                    n_placeholder=len(ph), n_real=len(real), n_iv_cand=len(sols))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('wasm')
    ap.add_argument('--tokens', default=None, help='样本令牌文件（每行一个 005_…）；不给则扫工作区')
    ap.add_argument('--no-scan', action='store_true', help='不扫工作区，只用 --tokens')
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('--verify', default=None, help='与已有 profile 对拍')
    a = ap.parse_args()

    toks = []
    if a.tokens:
        toks = [x.strip() for x in open(a.tokens, encoding='utf8') if x.strip()]
    elif not a.no_scan:
        toks = collect_tokens()
    r = derive(a.wasm, toks)
    if not r:
        print('[derive] 失败：池里找不到 ≥4 条结构一致的占位令牌')
        return 1
    print('[derive] wasm = %s' % os.path.basename(a.wasm))
    print('[derive] 占位令牌 %d 条，真令牌 %d 条，iv 候选 %d 个'
          % (r['n_placeholder'], r['n_real'], r['n_iv_cand']))
    print('[derive] xreg=%#04x  mask=%#04x  iv=%s' % (r['xreg'], r['mask'], r['iv']))
    print('[derive] rk[0..3] = %s' % [hex(x) for x in r['rk'][:4]])
    print('[derive] alphabet = %s' % r['alphabet'])
    if not r['iv_unique']:
        print('[derive] iv[0:4] 有 %d 个候选（只影响 record93 的 w0，服务端不校验 ⇒ 取第一个即可）：%s'
              % (r['n_iv_cand'], r['iv_candidates'][:6]))
    if a.out and r['iv']:
        json.dump({'rk': r['rk'], 'alphabet': r['alphabet'], 'xreg': r['xreg'],
                   'mask': r['mask'], 'iv': r['iv']},
                  open(a.out, 'w', encoding='utf8'), ensure_ascii=False, indent=1)
        print('[derive] -> %s' % a.out)
    if a.verify and r['iv']:
        ref = json.load(open(a.verify, encoding='utf8'))
        ok = (r['rk'] == ref['rk'] and r['alphabet'] == ref['alphabet']
              and r['xreg'] == ref['xreg'] and r['mask'] == ref['mask'] and r['iv'] == ref['iv'])
        print('[derive] 对拍 %s：%s' % (os.path.basename(a.verify), 'MATCH' if ok else 'DIFF'))
        return 0 if ok else 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
