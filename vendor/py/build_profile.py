#!/usr/bin/env python3
"""任意 build 的「纯算参数」全自动提取器（不解析任何函数，不依赖 trace）

思路（这就是捷径）
------------------
整个 ck 生成是 **(build, 环境) 的确定性函数**：

    ck = '005_' + radix64_encode( encrypt_96(record93 + PKCS#7(3), rk, iv, mask),
                                  alphabet, xreg )

其中 **只与 build 有关的量**只有 3 个：`rk`(32×u32)、`alphabet`(64 字符)、`xreg`；
`iv` / `mask` 目前实测恒为默认值（但仍全量搜索，不做假设）。

所以不需要读懂任何 Go 函数 / VM 字节码：**跑一次驱动 → 落内存 → 自动抠出这 3 个量**，
再用「能否逐字符重算出刚产出的 ck」**自证**（自证失败就不写 profile）。

用法
----
  # 1) 对某个 build 跑驱动并落内存（用现有驱动，任何 build 都行）
  ZSE_ROOT=. WASM=<build>.wasm NOSUPPLY=1 EVLIM=3 node <驱动>      # 产出 out/mem_mine.bin

  # 2) 一条命令抠参数（自证通过才写）
  python py/build_profile.py out/mem_mine.bin -o profiles/<build>.json

  # 3) 之后任意次纯算
  python py/pure_ck.py profiles/<build>.json <record93>

产物 profile.json: {rk[32], alphabet(64), xreg, iv, mask, record93, ck}
"""
import re, sys, json, os, argparse, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
CHARSET = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')


def load_cipher():
    for c in (os.path.join(ROOT, '.probe', 'vmsvc', 'zse_cipher.py'),
              os.path.join(HERE, 'zse_cipher.py')):
        if os.path.exists(c):
            spec = importlib.util.spec_from_file_location('zc', c)
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            return m
    raise SystemExit('找不到 zse_cipher.py')


def alphabet_candidates(buf):
    """内存里所有 64 字符排列候选（radix64 字母表必然是其中一个）。"""
    out = []
    for m in re.finditer(rb'[A-Za-z0-9+/=]{64,}', buf):
        w = m.group()
        for off in range(0, min(len(w) - 63, 64)):
            t = w[off:off + 64].decode()
            if len(set(t)) == 64 and set(t) <= CHARSET and t not in out:
                out.append(t)
    return out


def pair_list(buf):
    """Decimal strings that appear twice, 0x10 apart, sorted by address."""
    pat = re.compile(rb'(?<![0-9])([0-9]{5,10})(?![0-9])')
    raw = [(m.start(), int(m.group(1))) for m in pat.finditer(buf)]
    pos = {}
    for a, v in raw:
        pos.setdefault(v, []).append(a)
    pairs, seen = [], set()
    for a, v in raw:
        if a in seen:
            continue
        if a + 0x10 in pos.get(v, []):
            pairs.append((a, v)); seen.add(a); seen.add(a + 0x10)
    pairs.sort()
    return pairs


def extract_rk(buf):
    """32 个十进制串：每个出现两次、地址差 0x10 ⇒ rk = 值 ^ 0x7B7B7B7B。"""
    pat = re.compile(rb'(?<![0-9])([0-9]{5,10})(?![0-9])')
    raw = [(m.start(), int(m.group(1))) for m in pat.finditer(buf)]
    pos = {}
    for a, v in raw:
        pos.setdefault(v, []).append(a)
    pairs, seen = [], set()
    for a, v in raw:
        if a in seen:
            continue
        if a + 0x10 in pos.get(v, []):
            pairs.append((a, v)); seen.add(a); seen.add(a + 0x10)
    pairs.sort()
    best = None
    i = 0
    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and pairs[j][0] - pairs[j - 1][0] <= 0x200:
            j += 1
        seg = pairs[i:j]
        if len(seg) >= 32 and len(set(v for _, v in seg)) >= 30:
            if best is None or len(seg) > len(best):
                best = seg
        i = j
    if not best or len(best) < 32:
        return None
    return [v ^ 0x7B7B7B7B for _, v in best[:32]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mem')
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('--iv', default='d19e4a2bc57f8603', help='16 ASCII 字节，非 hex')
    a = ap.parse_args()

    zc = load_cipher()
    buf = open(a.mem, 'rb').read()
    rec = re.search(rb'[\x20-\x7e]{48}[0-9a-f]{32}z[0-9a-f]{12}', buf)
    cks = list(set(re.findall(rb'005_[A-Za-z0-9+/=]{128}', buf)))
    if not rec or len(cks) != 1:
        print('内存里 record93 / ck 不唯一：rec=%s ck=%d' % (bool(rec), len(cks))); return 2
    rec = rec.group(); ck = cks[0].decode()
    pt = zc.record93_to_pt(rec)
    iv = a.iv.encode('latin1')

    # IV-FREE self-check: `iv` is a per-session harness value and
    #   IV = D(C_0) ^ P_0 ^ mask  is an identity, so CBC blocks 1..5 do NOT depend on iv:
    #       P_b = D(C_b) ^ C_{b-1} ^ mask        (b >= 1)
    # The old version hardcoded 859e's iv, so every other build/session failed
    # (that is exactly why the 47f8 profile could not be extracted for a long time).
    def check_iv_free(ct, rk):
        d1 = zc.decrypt_block(ct[16:32], rk, zc.SBOX)
        x = bytes(p ^ q for p, q in zip(d1, ct[0:16]))
        mask = x[0] ^ pt[16]
        if any((x[i] ^ pt[16 + i]) != mask for i in range(16)):
            return None
        for b in range(2, 6):
            db = zc.decrypt_block(ct[16 * b:16 * b + 16], rk, zc.SBOX)
            y = bytes(p ^ q ^ mask for p, q in zip(db, ct[16 * (b - 1):16 * b]))
            if y != pt[16 * b:16 * b + 16]:
                return None
        d0 = zc.decrypt_block(ct[0:16], rk, zc.SBOX)
        return mask, bytes(p ^ q ^ mask for p, q in zip(d0, pt[:16]))

    vals = [v for _, v in pair_list(buf)]
    rk_wins = [vals[i:i + 32] for i in range(max(0, len(vals) - 31))]
    if not rk_wins:
        print('no 32-pair window found'); return 3
    print('rk windows: %d  rk0[0]=%#x' % (len(rk_wins), rk_wins[0][0] ^ 0x7B7B7B7B))

    alphas = alphabet_candidates(buf)
    print('alphabet candidates: %d' % len(alphas))

    for wi, w in enumerate(rk_wins):
        rk = [v ^ 0x7B7B7B7B for v in w]
        for al in alphas:
            for xr in range(256):
                try:
                    ct = zc.radix64_decode(ck[4:], al, xr)
                except Exception:
                    continue
                if len(ct) != 96:
                    continue
                r = check_iv_free(ct, rk)
                if r is None:
                    continue
                mask, ivd = r
                prof = {'rk': rk, 'alphabet': al, 'xreg': xr,
                        'iv': ivd.decode('latin1'), 'mask': mask,
                        'record93': rec.decode(), 'ck': ck}
                print('')
                print('SELF-CHECK OK (IV-free: verified CBC blocks 1..5, iv derived)')
                print('  window=%d  alphabet = %s' % (wi, al))
                print('  mask=%#x  xreg=%#x  iv=%r' % (mask, xr, ivd))
                if a.out:
                    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
                    json.dump(prof, open(a.out, 'w'), indent=1)
                    print('  wrote %s' % a.out)
                return 0
    print('self-check FAILED: alphabet not in memory, or rk not in the +0x10 pairs')
    return 1


if __name__ == '__main__':
    sys.exit(main())
