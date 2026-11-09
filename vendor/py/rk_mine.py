# -*- coding: utf-8 -*-
"""用本会话自己的 wasm 断点轨迹算 rk（等价于 zse_ck_tool.extract，但轨迹来自 Node 驱动的 fd_write 断点）。
轨迹格式: [opcode, sp, pr, v0lo, v0hi, v1lo, v1hi, v2lo, v2hi, v3lo, v3hi]
  - 捕获里 extract 用的 field[3]  == 本轨迹的 v0hi（栈顶元素高 32 位）
  - opcode==1 的连续段: 长度 16 => 序言(初态)，长度 8 且后 4 字节为 [30,22,14,8] => 一轮
自检: generate_token(profile, rec93) == 本会话 ck
"""
import json, io, re, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import zse_ck_tool as T

COL = 4  # 捕获 field[3] 对应本轨迹的列


def load_trace(p):
    return json.load(io.open(p, encoding='utf-8'))


def runs_of(t):
    runs = []; i = 0
    while i < len(t):
        if t[i][0] == 1:
            j = i
            while j < len(t) and t[j][0] == 1:
                j += 1
            runs.append((i, j)); i = j
        else:
            i += 1
    return runs


def main():
    tp = os.path.join(ROOT, '.probe/zsec/bp_trace.json')
    t = load_trace(tp)
    runs = runs_of(t)
    rounds = []; pro = []
    for a, b in runs:
        v = [t[k][COL] for k in range(a, b)]
        if b - a == 8 and v[4:] == [30, 22, 14, 8]:
            rounds.append(v[:4])
        if b - a == 16:
            pro.append(bytes(v))
    print('轨迹 %d 条；轮=%d 序言=%d' % (len(t), len(rounds), len(pro)))
    if len(rounds) != 192 or len(pro) != 6:
        print('结构不符（rounds=%d prologues=%d）' % (len(rounds), len(pro))); return

    pot = json.load(io.open(os.path.join(ROOT, '.probe/vmsvc/sbox_full.json')))['pot']
    sbox = [0] * 256
    for v, p in enumerate(pot):
        sbox[p] = v

    X = []
    for bi in range(6):
        Xa = [int.from_bytes(pro[bi][4 * k:4 * k + 4], 'big') for k in range(4)]
        Ab = [int.from_bytes(bytes(rounds[bi * 32 + r]), 'big') for r in range(32)]
        for r in range(32):
            Xa.append(Xa[r] ^ T._L(Ab[r]))
        X.append(Xa)
    rk = [int.from_bytes(bytes(pot[b] for b in rounds[r]), 'big')
          ^ X[0][r + 1] ^ X[0][r + 2] ^ X[0][r + 3] for r in range(32)]
    print('rk[0:4] = %s' % [hex(x) for x in rk[:4]])

    mem = open(os.path.join(ROOT, '.probe/zsec/mem_build.bin'), 'rb').read()
    m = re.search(rb'[\x20-\x7e]{48}[0-9a-f]{32}z[0-9a-f]{12}', mem)
    if not m:
        print('内存里没有 record93'); return
    rec = m.group()
    ck = io.open(os.path.join(ROOT, '.probe/zsec/ck_build.txt'), encoding='utf-8').read().strip()
    print('record93 = %s' % rec.decode())
    print('本会话 ck = %s…' % ck[:32])

    pt = rec + bytes([3, 3, 3])
    ms = set()
    for b in range(5):
        c = T.encrypt_block(pro[b], rk, sbox)
        for x, y, z in zip(c, pro[b + 1], pt[16 * (b + 1):16 * (b + 1) + 16]):
            ms.add(x ^ y ^ z)
    print('掩码候选数 = %d' % len(ms))
    if len(ms) != 1:
        print('掩码反解不一致 -> rk 不对'); return
    mask = list(ms)[0]
    iv = bytes(x ^ y ^ mask for x, y in zip(pro[0], pt[:16]))
    print('mask=%#x iv=%r' % (mask, iv))

    payload = ck[4:]
    seq = T.encrypt_96(pt, rk, iv, mask, sbox)[::-1]
    best = None
    for XR in range(256):
        mm = {}; bad = 0
        for g in range(32):
            base = (0, 24, 16, 8)[g % 4]; v = 0
            for j in range(3):
                v |= seq[g * 3 + j] << ((base + 8 * j) % 32)
            v = (v & 0xffffffff) ^ XR
            for k in range(4):
                idx = T._circ(v, (base + 6 * k) % 32); ch = payload[g * 4 + k]
                if idx in mm:
                    if mm[idx] != ch:
                        bad += 1
                else:
                    mm[idx] = ch
        if best is None or bad < best[0]:
            best = (bad, XR, mm)
    bad, XR, mm = best
    print('radix64 冲突数 = %d  xreg = %#x' % (bad, XR))
    alpha = ''.join(mm.get(i, '?') for i in range(64))
    if '?' in alpha:
        STD = set(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')
        for m2 in re.finditer(rb'[A-Za-z0-9+/=]{64}', mem):
            s64 = m2.group()
            if len(set(s64)) == 64 and set(s64) <= STD:
                cand = s64.decode()
                if all(cand[i] == ch for i, ch in mm.items()):
                    alpha = cand; break
    print('alphabet = %s' % alpha)
    if '?' in alpha:
        print('字母表只解出 %d/64' % (64 - alpha.count('?'))); return

    prof = dict(rk=rk, mask=mask, iv=iv.decode('latin1'), xreg=XR, alphabet=alpha)
    got = T.generate_token(prof, rec.decode())
    print()
    print('自检 gen(profile, rec93) == 本会话 ck : %s' % (got == ck))
    print('  得到 %s…' % got[:40])
    print('  期望 %s…' % ck[:40])
    if got == ck:
        json.dump(dict(prof, rec93=rec.decode(), token=ck),
                  io.open(os.path.join(ROOT, '.probe/zsec/my_profile.json'), 'w'), indent=1)
        print('已写 .probe/zsec/my_profile.json  <- 现在可以用 gen 自造任意 record93')


if __name__ == '__main__':
    main()
