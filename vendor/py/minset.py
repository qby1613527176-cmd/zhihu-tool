# -*- coding: utf-8 -*-
"""一次跑完：取挑战→打补丁→取 rk→从「通过位图」出发逐位加回我的位→提交。
目的：判定服务端到底校验哪几位（避免 meta 过期 / 风控）。"""
import subprocess, io, re, os, sys, time, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import zse_ck_tool as T

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"
BODY = os.path.join(ROOT, '.probe/oracle/ctrl/ms.body')
TW = ['2000', '1000', '8000', '4030', '8000', '8000', '8800', '8000', '8000', '8000', '4000']
EXTRA = [(1, 3), (1, 1), (1, 0), (4, 13), (4, 11), (8, 11), (8, 9), (10, 14)]


def curl(a):
    return subprocess.run(['curl', '-s'] + a, capture_output=True, text=True).stdout


def get(cookie=None):
    a = ['-s', '-o', BODY, '-w', '%{http_code}', '-H', 'User-Agent: ' + UA]
    if cookie is not None:
        a += ['-H', 'Cookie: __zse_ck=' + cookie]
    a += [ACT]
    http = curl(a).strip()
    b = io.open(BODY, 'rb').read().decode('utf-8', 'replace')
    if '暂时限制本次访问' in b or '"code":40362' in b:
        return http, '40362风控', b
    if 'zh-zse-ck' in b:
        return http, '403挑战页', b
    return http, '302通过', b


def main():
    t0 = time.time()
    _, _, body = get()
    m = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', body)
    b = re.search(r'zse-ck/v4/([0-9a-f]{64})\.js', body)
    if not m or not b:
        print('取挑战失败'); return
    meta, bh = m.group(1), b.group(1)
    bp = os.path.join(ROOT, '.probe/oracle/live_ms.js')
    curl(['-o', bp, '-H', 'User-Agent: ' + UA, 'https://static.zhihu.com/zse-ck/v4/%s.js' % bh])
    pw = os.path.join(ROOT, '.probe/vmsvc/wasm_ms_bp.wasm')
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'py/wasm_patch.py'), bp, pw],
                       capture_output=True, text=True, cwd=ROOT)
    if '已写' not in r.stdout:
        print('补丁失败'); return
    env = dict(os.environ, BPWASM=pw, BUILD=bp, GTRACE='0', NOEVAL='1',
               BUILD_URL='https://static.zhihu.com/zse-ck/v4/%s.js' % bh, WAIT_MS='9000')
    subprocess.run(['node', os.path.join(ROOT, '.probe/zsec/run_build_live.js')],
                   capture_output=True, text=True, env=env, timeout=240, cwd=ROOT)
    ck = io.open(os.path.join(ROOT, '.probe/zsec/ck_build.txt'), encoding='utf-8').read().strip()
    print('[%5.1fs] 驱动跑完 %s…' % (time.time() - t0, ck[:26]))

    t = json.load(io.open(os.path.join(ROOT, '.probe/zsec/bp_trace.json'), encoding='utf-8'))
    runs = []; i = 0
    while i < len(t):
        if t[i][0] == 1:
            j = i
            while j < len(t) and t[j][0] == 1:
                j += 1
            runs.append((i, j)); i = j
        else:
            i += 1
    rounds = []; pro = []
    for a, b2 in runs:
        v = [t[k][4] for k in range(a, b2)]
        if b2 - a == 8 and v[4:] == [30, 22, 14, 8]:
            rounds.append(v[:4])
        if b2 - a == 16:
            pro.append(bytes(v))
    pot = json.load(io.open(os.path.join(ROOT, '.probe/vmsvc/sbox_global.json')))['pot']
    sbox = [0] * 256
    for v, q in enumerate(pot):
        sbox[q] = v
    X = []
    for bi in range(6):
        Xa = [int.from_bytes(pro[bi][4 * k:4 * k + 4], 'big') for k in range(4)]
        Ab = [int.from_bytes(bytes(rounds[bi * 32 + r]), 'big') for r in range(32)]
        for r in range(32):
            Xa.append(Xa[r] ^ T._L(Ab[r]))
        X.append(Xa)
    rk = [int.from_bytes(bytes(pot[bb] for bb in rounds[r]), 'big')
          ^ X[0][r + 1] ^ X[0][r + 2] ^ X[0][r + 3] for r in range(32)]
    mem = open(os.path.join(ROOT, '.probe/zsec/mem_build.bin'), 'rb').read()
    rec = re.search(rb'[\x20-\x7e]{48}[0-9a-f]{32}z[0-9a-f]{12}', mem).group().decode()
    pt = rec.encode() + bytes([3, 3, 3])
    ms = set()
    for bb in range(5):
        c = T.encrypt_block(pro[bb], rk, sbox)
        for x, y, z in zip(c, pro[bb + 1], pt[16 * (bb + 1):16 * (bb + 1) + 16]):
            ms.add(x ^ y ^ z)
    mask = list(ms)[0]
    iv = bytes(x ^ y ^ mask for x, y in zip(pro[0], pt[:16]))
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
    alpha = ''.join(mm.get(i, '?') for i in range(64))
    if '?' in alpha:
        STD = set(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')
        for m2 in re.finditer(rb'[A-Za-z0-9+/=]{64}', mem):
            s64 = m2.group()
            if len(set(s64)) == 64 and set(s64) <= STD:
                cand = s64.decode()
                if all(cand[i] == ch for i, ch in mm.items()):
                    alpha = cand; break
    print('[%5.1fs] rk 就绪 冲突=%d 字母表=%d/64' % (time.time() - t0, bad, 64 - alpha.count('?')))

    def gen(r):
        return '005_' + T.radix64_encode(T.encrypt_96(r.encode() + bytes([3, 3, 3]), rk, iv, mask, sbox), alpha, XR)

    ok = (gen(rec) == ck)
    print('  自检 gen(rec93)==ck : %s' % ok)
    if not ok:
        print('  自检失败，退出'); return

    def test(tag, r):
        time.sleep(11)
        http, kind, _ = get(gen(r) + '-' + meta)
        print('  %-32s HTTP=%-4s %s' % (tag, http, kind), flush=True)

    print('=== 从「通过位图」出发，每次只加回我一个位 ===')
    test('目标位图（对照）', rec[:4] + ''.join(TW) + rec[48:])
    for w, bb in EXTRA:
        words = [rec[:4]] + TW[:]
        v = int(words[w], 16) | (1 << bb)
        words[w] = '%04x' % v
        test('加回 w%d bit%-2d -> %s' % (w, bb, words[w]), ''.join(words) + rec[48:])
    test('我的原位图（对照）', rec)
    print('[%5.1fs] 完成' % (time.time() - t0))


if __name__ == '__main__':
    main()
