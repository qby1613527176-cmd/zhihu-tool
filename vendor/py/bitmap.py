#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bitmap.py —— 「48-hex 位图」的纯算 Python 版

来源：`js/bitmap_assembler.js`（可运行还原件，逐字符自检通过）→ 本文件逐函数 1:1 移植。
不加载任何站点代码、不需要 Node。

结构（已逐字符验证）
--------------------
    record93 = [48-hex 位图][32-hex 指纹(md5)]['z'][8-hex crc32(UA)][4-hex tail]
    48-hex 位图 = 12 × 16-bit 字：
        w0      会话标签（每会话随机）
        w1..w4  ← payload 层 v3..v6      （16 hex）
        w5..w6  ← inner   层 v3,v4       （8  hex）
        w7..w9  ← Go(wasm) 侧自算 3 字    （12 hex，JS 侧无载体）
        w10..w11← cvm     层 v7,v8       （8  hex）

各层累加字初值（AST 静态提取）
    payload: v3=1<<13  v4=1<<12  v5=1<<15  v6=1<<14
    inner  : v3=1<<15  v4=1<<14
    cvm    : v7=1<<15  v8=1<<14  v9=1<<13  v10=1<<12
之后每个环境检测命中就 `vN |= (1<<k)`；逐条守卫清单见 `js/bitmap_detection.restored.js`
与 `docs/BITMAP.md`。

链式 XOR 流（payload FR_1832 / inner FR_869 / cvm FR_877，三者同构）
    out[i] = in[i] ^ key[i % len(key)] ^ out[i-1]      out[-1] = __b.a3
    __b.a3 = out[最后一位]                             ← 状态跨层、跨调用共享

用法
----
    from bitmap import decode_bitmap_from_zh_qh, build_record93
    d = decode_bitmap_from_zh_qh(zh, qh)          # -> {'payload16','inner8','cvm8','fp32'}
    rec = build_record93(tag4='2b0d', payload16=d['payload16'], inner8=d['inner8'],
                         go_side12='880080008000', cvm8=d['cvm8'],
                         fingerprint32=d['fp32'], crc8='3efa0afb', tail4='0434')

    python py/bitmap.py --selftest                # 用 .probe/zsec/zh_full.txt + qh_full.txt
"""
import argparse
import json
import os
import re

ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)

# ----------------------------------------------------------------- 常量
KEY = {
    'random':      'acc7471d3eb9bc30',   # zh[0]
    'payload':     'f25b8434cca9a1f5',   # zh[1] → 位图 w1..w4
    'inner':       'b53cc3fa93dc8013',   # zh[3] → 位图 w5..w6
    'cvm':         '6a6cb1bb3970bae6',   # qh[0] → 位图 w10..w11
    'fingerprint': '44ae0b7a68bf0f7a',   # qh[1] → record93 指纹段
}
SEP = {'payload': 0x200C, 'cvm': 0x200E, 'inner': 0x2008}

# 各层累加字初值（供调用方参考/构造）
DETECT_INIT = {
    'payload': {'v3': 1 << 13, 'v4': 1 << 12, 'v5': 1 << 15, 'v6': 1 << 14},
    'inner':   {'v3': 1 << 15, 'v4': 1 << 14},
    'cvm':     {'v7': 1 << 15, 'v8': 1 << 14, 'v9': 1 << 13, 'v10': 1 << 12},
}


# ----------------------------------------------------------------- 链式 XOR 流
def xstream(inp, key, state):
    """加密：out[i] = in[i] ^ key[i%k] ^ out[i-1]；state 为 out[-1]。"""
    out = []
    st = state
    for i, ch in enumerate(inp):
        c = (ord(ch) ^ ord(key[i % len(key)]) ^ st) & 0xFFFF
        st = c
        out.append(chr(c))
    return ''.join(out), st


def xdecode(ct, key, state_in):
    """解密：状态是前一个**密文**字符 ⇒ plain[i] = ct[i] ^ key[i%k] ^ ct[i-1]。"""
    out = []
    prev = state_in
    for i, ch in enumerate(ct):
        out.append(chr((ord(ch) ^ ord(key[i % len(key)]) ^ prev) & 0xFFFF))
        prev = ord(ch)
    return ''.join(out)


# ----------------------------------------------------------------- 层汇编
def layer_hex(words):
    return ''.join(('%04x' % (w & 0xFFFFFFFF)) for w in words)


def layer_payload16(acc):
    return layer_hex([acc['v3'], acc['v4'], acc['v5'], acc['v6']])


def layer_inner8(acc):
    return layer_hex([acc['v3'], acc['v4']])


def layer_cvm8(acc):
    return layer_hex([acc['v7'], acc['v8']])


# ----------------------------------------------------------------- 写 zh / qh
def build_zh(acc, nonce_hex, stack_bytes, a3):
    """zh = X(nonce) + SEP + X(payload16) + SEP + b64(stack) + SEP + X(inner8) + SEP_inner"""
    import base64
    P_ = chr(SEP['payload'])
    st = a3
    r1, st = xstream(nonce_hex, KEY['random'], st)
    r2, st = xstream(layer_payload16(acc), KEY['payload'], st)
    r3, st = xstream(layer_inner8(acc), KEY['inner'], st)
    b64 = base64.b64encode(stack_bytes).decode('ascii')
    zh = r1 + P_ + r2 + P_ + b64 + P_ + r3 + chr(SEP['inner'])
    return zh, st


def build_qh(acc, fingerprint32, a3):
    """qh = X(cvm8) + SEP_cvm + X(fp32)"""
    C = chr(SEP['cvm'])
    st = a3
    r1, st = xstream(layer_cvm8(acc), KEY['cvm'], st)
    r2, st = xstream(fingerprint32, KEY['fingerprint'], st)
    return r1 + C + r2, st


# ----------------------------------------------------------------- 反解
def decode_bitmap_from_zh_qh(zh, qh):
    """从 zh / qh 反解出 4 个 hex 段（离线验证用，与 tools/decode_zh.py 等价）。

    进入状态 = 前一个被 X 段的末字符；zh[2] 是明文 btoa，不参与链。
    """
    zp = zh.split(chr(SEP['payload']))
    qp = qh.split(chr(SEP['cvm']))
    st_zh1 = ord(zp[0][-1])
    st_zh3 = ord(zp[1][-1])
    st_qh0 = ord(zp[3][-1])
    st_qh1 = ord(qp[0][-1])
    return {
        'payload16': xdecode(zp[1], KEY['payload'], st_zh1),
        'inner8':    xdecode(zp[3], KEY['inner'], st_zh3),
        'cvm8':      xdecode(qp[0], KEY['cvm'], st_qh0),
        'fp32':      xdecode(qp[1], KEY['fingerprint'], st_qh1),
    }


# ----------------------------------------------------------------- 拼 record93
def build_record93(tag4, payload16, inner8, go_side12, cvm8,
                   fingerprint32, crc8, tail4):
    bitmap48 = tag4 + payload16 + inner8 + go_side12 + cvm8
    if len(bitmap48) != 48:
        raise ValueError('bitmap must be 48 hex, got %d' % len(bitmap48))
    return bitmap48 + fingerprint32 + 'z' + crc8 + tail4


# ----------------------------------------------------------------- 自检
def _load_fp():
    import importlib.util as _iu
    s = _iu.spec_from_file_location('fpg', P('py', 'fingerprint.py'))
    m = _iu.module_from_spec(s); s.loader.exec_module(m); return m


def _selftest():
    # 优先用自洽 fixture 里的 zh/qh：盘上的 zh_full.txt 会被
    #   跑驱动时覆盖成别的会话，与 fixture 的 record93 不同源就会假报失败。
    fx = P('profiles', '_live_fixture.json')
    zh = qh = None
    if os.path.exists(fx):
        try:
            _j = json.load(open(fx, encoding='utf8'))
            zh, qh = _j.get('zh'), _j.get('qh')
        except Exception:
            pass
    if not zh or not qh:
        zhf, qhf = P('.probe', 'zsec', 'zh_full.txt'), P('.probe', 'zsec', 'qh_full.txt')
        if not (os.path.exists(zhf) and os.path.exists(qhf)):
            print('未找到 zh/qh，跳过自检')
            return 0
        zh = open(zhf, encoding='utf8').read()
        qh = open(qhf, encoding='utf8').read()
    d = decode_bitmap_from_zh_qh(zh, qh)
    print('payload16 = %s  (w1..w4)' % d['payload16'])
    print('inner8    = %s  (w5..w6)' % d['inner8'])
    print('cvm8      = %s  (w10..w11)' % d['cvm8'])
    print('fp32      = %s' % d['fp32'])

    ok = True
    # 指纹只与环境有关，跨会话稳定；位图字段随环境/handenv 变，**不硬编码**
    # （位图随环境变；硬编码会在环境更新后过期）
    fp_expect = None
    try:
        fp_mod = _load_fp()
        fp_expect = fp_mod.fingerprint_from_preimage(fp_mod.load_default_preimage())
    except Exception:
        fp_expect = 'be544511d8accf5879de9179e434e829'
    good = (d['fp32'] == fp_expect)
    ok &= good
    print('  %-10s %s (期望 %s)' % ('fp32', 'OK' if good else 'MISMATCH', fp_expect))

    # 用**同一次会话**的 record93 交叉验证（默认取自洽 fixture；跨会话的内存 dump 不能用来比位图）
    rec = None
    fx = P('profiles', '_live_fixture.json')
    if os.path.exists(fx):
        try:
            rec = json.load(open(fx, encoding='utf8')).get('record93')
        except Exception:
            rec = None
    if rec is None:
        mem = P('out', 'mem_859e.bin')
        if os.path.exists(mem):
            m = re.search(rb'[0-9a-f]{48}[0-9a-f]{32}z[0-9a-f]{12}', open(mem, 'rb').read())
            rec = m.group().decode() if m else None
    if rec:
        if True:
            tag4 = rec[:4]
            go12 = rec[4 + 16 + 8:4 + 16 + 8 + 12]      # w7..w9（Go 侧，不在 zh/qh 里）
            rebuilt = tag4 + d['payload16'] + d['inner8'] + go12 + d['cvm8']
            exp48 = rec[:48]
            same = (rebuilt == exp48)
            ok &= same
            print('  record93 位图 = %s' % exp48)
            print('  反解拼回     = %s' % rebuilt)
            print('  逐字符一致: %s' % same)
    # 往返：用反解出的明文 + 进入状态重新加密，必须逐字符还原原密文段
    zp = zh.split(chr(SEP['payload']))
    qp = qh.split(chr(SEP['cvm']))
    rt = [
        ('zh[1]', xstream(d['payload16'], KEY['payload'], ord(zp[0][-1]))[0], zp[1]),
        ('zh[3]', xstream(d['inner8'], KEY['inner'], ord(zp[1][-1]))[0], zp[3]),
        ('qh[0]', xstream(d['cvm8'], KEY['cvm'], ord(zp[3][-1]))[0], qp[0]),
        ('qh[1]', xstream(d['fp32'], KEY['fingerprint'], ord(qp[0][-1]))[0], qp[1]),
    ]
    for name, got, want in rt:
        good = (got == want)
        ok &= good
        print('  往返 %-6s %s' % (name, 'OK' if good else 'MISMATCH'))
    print('SELFTEST %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--zh', default=None, help='直接给 zh 串，配合 --qh 反解位图')
    ap.add_argument('--qh', default=None)
    a = ap.parse_args()
    if a.zh and a.qh:
        zh = open(a.zh, encoding='utf8').read() if os.path.exists(a.zh) else a.zh
        qh = open(a.qh, encoding='utf8').read() if os.path.exists(a.qh) else a.qh
        d = decode_bitmap_from_zh_qh(zh, qh)
        for k, v in d.items():
            print('%s = %s' % (k, v))
        return 0
    if a.selftest or True:
        return _selftest()


if __name__ == '__main__':
    raise SystemExit(main())
