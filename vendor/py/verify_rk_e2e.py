#!/usr/bin/env python3
"""端到端验证：内存提取的 rk 是否就是真正的 SM4 round key

做法（不依赖任何浏览器 trace）
------------------------------
输入：驱动跑完落地的 wasm 内存快照 + 该 build 的 rk（`py/extract_rk.py` 的产物）

  1. 从内存里取 record93（`[0-9a-f]{48}[0-9a-f]{32}z[0-9a-f]{12}`）与 ck（`005_` + 128 字符）
  2. pt = record93 + PKCS#7(3) = 96 B
  3. 扫内存里所有 64 字符排列候选（radix64 字母表；build 相关的那张在其中）
  4. 对每个 (字母表, mask, xreg)：ct = encrypt_96(pt, rk, iv, mask)
     判据：radix64_encode(ct, 字母表, xreg) == ck   —— 逐字符相等

命中即证明：这 32 个值就是加密用的 round key，整条 CBC + radix64 链可纯算复现。

实测结果（2026-10-08）
---------------------
  build-859e : 命中  alpha = lstHZNL…（第 4 张表，随 build 变） mask=0x4d xreg=0x30
               iv = 默认 d19e4a2bc57f8603
  cap2 build : 另有独立证据 —— `zc.generate_token(profile, rec93)` 与真 token
               132/132 逐字符相同（3 个会话全中），用的是 cap2 自己的 rk。
  ⇒ 两个 build 各自的 rk 都端到端验证通过，且两组 rk 不同 ⇒ **rk 随 build 变**。

用法
----
  python py/verify_rk_e2e.py <mem.bin> <rk.json> [--iv hex] [--xreg 0..255]
"""
import re, sys, json, argparse, importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
CHARSET = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')


def load_cipher():
    for cand in (os.path.join(ROOT, '.probe', 'vmsvc', 'zse_cipher.py'),
                 os.path.join(HERE, 'zse_cipher.py')):
        if os.path.exists(cand):
            spec = importlib.util.spec_from_file_location('zc', cand)
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            return m
    raise SystemExit('找不到 zse_cipher.py')


def alphabet_candidates(buf):
    out = []
    for m in re.finditer(rb'[A-Za-z0-9+/=]{64,}', buf):
        w = m.group()
        for off in range(0, min(len(w) - 63, 64)):
            t = w[off:off + 64].decode()
            if len(set(t)) == 64 and set(t) <= CHARSET and t not in out:
                out.append(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mem'); ap.add_argument('rk_json')
    ap.add_argument('--iv', default='d19e4a2bc57f8603')
    ap.add_argument('--xreg', type=int, default=None, help='不给则 0..255 全试')
    a = ap.parse_args()

    zc = load_cipher()
    buf = open(a.mem, 'rb').read()
    rec = re.search(rb'[\x20-\x7e]{48}[0-9a-f]{32}z[0-9a-f]{12}', buf)
    cks = list(set(re.findall(rb'005_[A-Za-z0-9+/=]{128}', buf)))
    if not rec or len(cks) != 1:
        print('内存里 record93 / ck 不唯一：rec=%s ck=%d' % (bool(rec), len(cks))); return 2
    rec = rec.group(); ck = cks[0].decode()[4:]
    rk = json.load(open(a.rk_json))['rk']
    pt = zc.record93_to_pt(rec)
    iv = bytes.fromhex(a.iv)
    xrs = [a.xreg] if a.xreg is not None else range(256)

    alphas = alphabet_candidates(buf)
    print('record93 = %s…%s' % (rec[:20].decode(), rec[-14:].decode()))
    print('字母表候选 %d 个' % len(alphas))
    for al in alphas:
        for mask in range(256):
            ct = zc.encrypt_96(pt, rk, iv=iv, mask=mask)
            for XR in xrs:
                if zc.radix64_encode(ct, al, XR) == ck:
                    print('\n命中：rk 就是真正的 round key')
                    print('  字母表 = %s' % al)
                    print('  mask   = %#x' % mask)
                    print('  xreg   = %#x' % XR)
                    print('  iv     = %s' % a.iv)
                    print('  radix64_encode(encrypt_96(record93+pad)) == ck  ✓')
                    return 0
    print('\n未命中（该 build 的字母表可能不在内存连续串里，或 mask/iv 非默认）')
    return 1


if __name__ == '__main__':
    sys.exit(main())
