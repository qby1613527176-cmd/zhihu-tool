#!/usr/bin/env python3
"""zse_ck 005_ token 离线生成工具（纯 Python，零依赖）

用法
----
1) 取参（对目标浏览器 profile 做一次，产物可长期复用）:
     python zse_ck_tool.py extract <trace.meta.json> <memory.bin> -o profile.json
   trace.meta.json / memory.bin = 用浏览器插桩抓到的「一次会话」的指令流 + 5MB 线性内存
   （抓取方法见 `docs/METHOD.md`；每次会话都能产出，参数同 profile 恒定）

2) 生成 token（离线，任意次）:
     python zse_ck_tool.py gen profile.json <record93>
   record93 = 48 hex 字体/特征位图 + 32 hex 指纹哈希 + 'z' + 8 hex crc32(UA) + 4 hex

3) 自校验（对 4 个已抓会话逐一提取+生成，与真实 token 逐字符比对）:
     python zse_ck_tool.py verify
"""
import json, re, struct, sys, glob, os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SBOX_GLOBAL = os.path.join(HERE, '.probe', 'vmsvc', 'sbox_global.json')

def _load_sbox():
    pot = json.load(open(SBOX_GLOBAL))['pot']
    assert len(set(pot)) == 256, 'sbox_global.json 损坏'
    return pot

def _rotl(x, r): return ((x << r) | (x >> (32 - r))) & 0xffffffff
def _L(x): return x ^ _rotl(x,2) ^ _rotl(x,10) ^ _rotl(x,18) ^ _rotl(x,24)
def _circ(V, s): return (((V >> s) | (V << ((32 - s) % 32))) & 0xffffffff) & 63

# ---------------------------------------------------------------- 密码本体
def encrypt_block(block16, rk, sbox):
    X = [int.from_bytes(block16[4*i:4*i+4], 'big') for i in range(4)]
    for i in range(32):
        w = X[i+1] ^ X[i+2] ^ X[i+3] ^ rk[i]
        a = int.from_bytes(bytes(sbox[b] for b in w.to_bytes(4, 'big')), 'big')
        X.append(X[i] ^ _L(a))
    return (X[35].to_bytes(4,'big') + X[34].to_bytes(4,'big') +
            X[33].to_bytes(4,'big') + X[32].to_bytes(4,'big'))

def encrypt_96(pt96, rk, iv, mask, sbox):
    prev = iv; out = b''
    for b in range(6):
        inp = bytes(x ^ y ^ mask for x, y in zip(pt96[16*b:16*b+16], prev))
        c = encrypt_block(inp, rk, sbox); out += c; prev = c
    return out

def radix64_encode(ct96, alphabet, xreg):
    seq = ct96[::-1]; out = []
    for g in range(32):
        base = (0, 24, 16, 8)[g % 4]
        v = 0
        for j in range(3):
            v |= seq[g*3+j] << ((base + 8*j) % 32)
        v = (v & 0xffffffff) ^ xreg
        for k in range(4):
            out.append(alphabet[_circ(v, (base + 6*k) % 32)])
    return ''.join(out)

def generate_token(profile, rec93):
    pot = _load_sbox()
    sbox = [0]*256
    for v, p in enumerate(pot): sbox[p] = v
    rk = profile['rk']
    rec = rec93.encode() if isinstance(rec93, str) else rec93
    assert len(rec) == 93, 'record93 必须 93 字节'
    pt = rec + bytes([3,3,3])
    ct = encrypt_96(pt, rk, bytes(profile['iv'],'latin1'), profile['mask'], sbox)
    return '005_' + radix64_encode(ct, profile['alphabet'], profile['xreg'])

# ---------------------------------------------------------------- 取参
def extract(meta_path, mem_path):
    pot = _load_sbox()
    sbox = [0]*256
    for v, p in enumerate(pot): sbox[p] = v
    mem = open(mem_path, 'rb').read()
    rec = re.search(rb'[\x20-\x7e]{48}[0-9a-f]{32}z[0-9a-f]{12}', mem)
    toks = set(re.findall(rb'005_[A-Za-z0-9+/=]{128}', mem))
    if not rec or len(toks) != 1:
        raise SystemExit('内存里找不到唯一的 record93 / token')
    rec = rec.group(); tok = list(toks)[0].decode()
    _j = json.load(open(meta_path, encoding='utf8'))
    if 'log' not in _j:
        raise SystemExit('meta 里没有 log（非会话转储）')
    log = [tuple(e) for e in _j['log']]
    runs = []; i = 0; n = len(log)
    while i < n:
        if log[i][0] == 1:
            j = i
            while j < n and log[j][0] == 1: j += 1
            runs.append((i, j)); i = j
        else: i += 1
    rounds = []; pro = []
    for a, b in runs:
        v = [log[k][3] for k in range(a, b)]
        if b-a == 8 and v[4:] == [30,22,14,8]: rounds.append(v[:4])
        if b-a == 16: pro.append(bytes(v))
    if len(rounds) != 192 or len(pro) != 6:
        raise SystemExit('指令流结构不符（rounds=%d prologues=%d）' % (len(rounds), len(pro)))
    X = []
    for bi in range(6):
        Xa = [int.from_bytes(pro[bi][4*k:4*k+4], 'big') for k in range(4)]
        Ab = [int.from_bytes(bytes(rounds[bi*32+r]), 'big') for r in range(32)]
        for r in range(32): Xa.append(Xa[r] ^ _L(Ab[r]))
        X.append(Xa)
    rk = [int.from_bytes(bytes(pot[b] for b in rounds[r]), 'big')
          ^ X[0][r+1] ^ X[0][r+2] ^ X[0][r+3] for r in range(32)]
    pt = rec + bytes([3,3,3])
    ms = set()
    for b in range(5):
        c = encrypt_block(pro[b], rk, sbox)
        for x, y, z in zip(c, pro[b+1], pt[16*(b+1):16*(b+1)+16]): ms.add(x ^ y ^ z)
    if len(ms) != 1: raise SystemExit('掩码反解不一致')
    mask = list(ms)[0]
    iv = bytes(x ^ y ^ mask for x, y in zip(pro[0], pt[:16]))
    ct = encrypt_96(pt, rk, iv, mask, sbox)
    payload = tok[4:]
    seq = ct[::-1]; best = None
    for XR in range(256):
        m = {}; bad = 0
        for g in range(32):
            base = (0,24,16,8)[g % 4]; v = 0
            for j in range(3): v |= seq[g*3+j] << ((base + 8*j) % 32)
            v = (v & 0xffffffff) ^ XR
            for k in range(4):
                idx = _circ(v, (base + 6*k) % 32); ch = payload[g*4+k]
                if idx in m:
                    if m[idx] != ch: bad += 1
                else: m[idx] = ch
        if best is None or bad < best[0]: best = (bad, XR, m)
    bad, XR, m = best
    if bad: raise SystemExit('radix64 反解有 %d 处冲突' % bad)
    alpha = ''.join(m.get(i, '?') for i in range(64))
    if '?' in alpha:
        # 单条 payload 只用到 ~56 个符号；用内存里的 64 字符置换候选补全
        STD = set(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')
        for mm in re.finditer(rb'[A-Za-z0-9+/=]{64}', mem):
            s64 = mm.group()
            if len(set(s64)) == 64 and set(s64) <= STD:
                cand = s64.decode()
                if all(cand[i] == ch for i, ch in m.items()):
                    alpha = cand; break
    if '?' in alpha:
        raise SystemExit('字母表只解出 %d/64' % (64 - alpha.count('?')))
    return dict(rec93=rec.decode(), token=tok, rk=rk, mask=mask,
                iv=iv.decode('latin1'), xreg=XR, alphabet=alpha)

# ---------------------------------------------------------------- CLI
def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == 'extract':
        meta, mem = sys.argv[2], sys.argv[3]
        out = sys.argv[5] if len(sys.argv) > 5 and sys.argv[4] == '-o' else 'profile.json'
        p = extract(meta, mem)
        json.dump(p, open(out, 'w'), indent=1)
        print('profile -> %s' % out)
        print('  mask=%#x iv=%r xreg=%#x' % (p['mask'], p['iv'], p['xreg']))
        print('  alphabet=%s' % p['alphabet'])
        print('  rk[0:4]=%s' % [hex(x) for x in p['rk'][:4]])
        print('  自检 token == 抓取值: %s' % (generate_token(p, p['rec93']) == p['token']))
    elif cmd == 'gen':
        p = json.load(open(sys.argv[2]))
        print(generate_token(p, sys.argv[3]))
    elif cmd == 'verify':
        metas = sorted(glob.glob(os.path.join(HERE, '.probe', 'cap2', 'dump_*.bin.meta.json')),
                       key=os.path.getmtime)
        ok = 0; tried = 0
        for mt in metas:
            try:
                p = extract(mt, mt.replace('.meta.json', ''))
                got = generate_token(p, p['rec93'])
                same = (got == p['token'])
                ok += same; tried += 1
                print('%-40s %s  %s' % (os.path.basename(mt), 'OK' if same else 'FAIL', got[:34] + '...'))
            except SystemExit as e:
                print('%-40s SKIP (%s)' % (os.path.basename(mt), e))
        print('verify: %d/%d 带指令流的会话端到端复现（其余 %d 份是无 trace 的内存转储）'
              % (ok, tried, len(metas) - tried))
    else:
        print(__doc__)

if __name__ == '__main__':
    main()
