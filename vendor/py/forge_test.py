# -*- coding: utf-8 -*-
"""端到端锻造实验：
 1) 取挑战(meta+build) → 下载 build → 打补丁 → 跑驱动拿轨迹/内存/ck
 2) 从轨迹算 rk（并用「编码回去 == ck」自检）
 3) 用同一 rk 自造 record93 变体，逐个提交真站，看 403 / 302 的分界
"""
import subprocess, io, re, os, sys, time, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import zse_ck_tool as T

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"
BODY = os.path.join(ROOT, '.probe/oracle/ctrl/ft.body')
TARGET_W = ['2000', '1000', '8000', '4030', '8000', '8000', '8800', '8000', '8000', '8000', '4000']


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
        return http, '风控', b
    if 'zh-zse-ck' in b:
        return http, '挑战页', b
    return http, '通过', b


def solve_rk(trace_path):
    t = json.load(io.open(trace_path, encoding='utf-8'))
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
    for a, b in runs:
        v = [t[k][4] for k in range(a, b)]
        if b - a == 8 and v[4:] == [30, 22, 14, 8]:
            rounds.append(v[:4])
        if b - a == 16:
            pro.append(bytes(v))
    if len(rounds) != 192 or len(pro) != 6:
        return None, None, None, '结构不符 rounds=%d pro=%d' % (len(rounds), len(pro))
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
    return rk, pro, sbox, None


def main():
    t0 = time.time()
    http, kind, body = get()
    m = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', body)
    b = re.search(r'zse-ck/v4/([0-9a-f]{64})\.js', body)
    if not m or not b:
        print('取挑战失败: HTTP=%s 类型=%s' % (http, kind)); return
    meta, bh = m.group(1), b.group(1)
    print('[%5.1fs] 挑战 meta=%d build=%s' % (time.time() - t0, len(meta), bh[:16]))

    bp = os.path.join(ROOT, '.probe/oracle/live_forge.js')
    curl(['-o', bp, '-H', 'User-Agent: ' + UA, 'https://static.zhihu.com/zse-ck/v4/%s.js' % bh])

    pw = os.path.join(ROOT, '.probe/vmsvc/wasm_forge_bp.wasm')
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'py/wasm_patch.py'), bp, pw],
                       capture_output=True, text=True, cwd=ROOT)
    if '已写' not in r.stdout:
        print('补丁失败:'); print(r.stdout[-500:]); return
    print('[%5.1fs] wasm 补丁完成' % (time.time() - t0))

    env = dict(os.environ, BPWASM=pw, BUILD=bp, GTRACE='0', NOEVAL='1',
               BUILD_URL='https://static.zhihu.com/zse-ck/v4/%s.js' % bh, WAIT_MS='9000')
    subprocess.run(['node', os.path.join(ROOT, '.probe/zsec/run_build_live.js')],
                   capture_output=True, text=True, env=env, timeout=240, cwd=ROOT)
    ck = io.open(os.path.join(ROOT, '.probe/zsec/ck_build.txt'), encoding='utf-8').read().strip()
    print('[%5.1fs] 驱动跑完 ck=%s…' % (time.time() - t0, ck[:32]))

    rk, pro, sbox, err = solve_rk(os.path.join(ROOT, '.probe/zsec/bp_trace.json'))
    if err:
        print('取 rk 失败:', err); return
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
                    alpha = cand
                    print('  字母表已用内存置换候选补全')
                    break
    print('[%5.1fs] rk 就绪 mask=%#x xreg=%#x 冲突=%d 字母表=%d/64' % (
        time.time() - t0, mask, XR, bad, 64 - alpha.count('?')))
    if '?' in alpha or bad:
        print('字母表未全解，退出'); return

    def gen(rec93):
        p = rec93.encode() + bytes([3, 3, 3])
        return '005_' + T.radix64_encode(T.encrypt_96(p, rk, iv, mask, sbox), alpha, XR)

    print('  自检 gen(rec93) == ck : %s' % (gen(rec) == ck))
    print('  record93 = %s' % rec)

    variants = [('基线（我的 record93）', rec)]
    tb = rec[:4] + ''.join(TARGET_W)
    variants.append(('位图换真机常量', tb + rec[48:]))
    variants.append(('位图换真机常量+w0 也换', '36c0' + ''.join(TARGET_W) + rec[48:]))
    variants.append(('基线（复测，排风控）', rec))
    variants.append(('位图换真机常量（复测）', tb + rec[48:]))

    json.dump(dict(rk=rk, mask=mask, iv=iv.decode('latin1'), xreg=XR, alphabet=alpha,
                   rec93=rec, token=ck, meta=meta),
              io.open(os.path.join(ROOT, '.probe/zsec/forge_profile.json'), 'w'), indent=1)
    print('  已写 .probe/zsec/forge_profile.json')

    for tag, r2 in variants:
        time.sleep(12)
        ck2 = gen(r2)
        http, kind, body = get(ck2 + '-' + meta)
        print('  %-24s HTTP=%-4s 类型=%-6s len=%d  %s' % (tag, http, kind, len(body), ck2[:26]))
    print('[%5.1fs] 完成' % (time.time() - t0))


if __name__ == '__main__':
    main()
