# -*- coding: utf-8 -*-
"""关键判定：ck 是否与「生成它时用的那个 meta」绑定。
  ck(M1) + M1  -> 预期 40362（已知）
  ck(M1) + M2  -> 若 694 ⇒ 绑定在客户端/挑战记录；若 40362 ⇒ meta 只是松散检查
"""
import subprocess, io, os, re, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"

def curl(args): return subprocess.run(['curl', '-s'] + args, capture_output=True, text=True).stdout

def fetch_challenge():
    h = curl(['-H', 'User-Agent: ' + UA, ACT])
    m = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', h)
    b = re.search(r'zse-ck/v4/([0-9a-f]{64})\.js', h)
    return (m.group(1) if m else None), (b.group(1) if b else None)

def gen(meta, hash_):
    io.open('.probe/oracle/meta2.txt', 'w', encoding='utf-8').write(meta)
    bp = '.probe/oracle/builds/%s.js' % hash_[:16]
    curl(['-o', bp, '-H', 'User-Agent: ' + UA, 'https://static.zhihu.com/zse-ck/v4/%s.js' % hash_])
    env = dict(os.environ, BUILD=bp, GTRACE='0', NOEVAL='1', WAIT_MS='9000',
               BUILD_URL='https://static.zhihu.com/zse-ck/v4/%s.js' % hash_)
    r = subprocess.run(['node', '.probe/zsec/run_build_live.js'], capture_output=True, text=True, env=env, timeout=200)
    ck = io.open('.probe/zsec/ck_build.txt', encoding='utf-8').read().strip()
    err = (re.search(r'错误数: (\d+)', r.stdout) or ['', '?'])[1]
    print('  生成: 错误数=%s ck=%d  (用 meta 头 %s…)' % (err, len(ck), meta[:12]))
    return ck

def probe(tag, ck, meta):
    time.sleep(6)
    out = subprocess.run(['curl', '-s', '-o', '.probe/oracle/ctrl/mm.body', '-w', '%{http_code} %{size_download}',
                          '-H', 'User-Agent: ' + UA, '-H', 'Cookie: __zse_ck=' + ck + '-' + meta, ACT],
                         capture_output=True, text=True).stdout.strip()
    body = io.open('.probe/oracle/ctrl/mm.body', 'rb').read().decode('utf-8', 'replace')
    jc = re.search(r'"code"\s*:\s*(\d+)', body)
    kind = '302通过' if out.startswith('302') else ('694挑战页' if '694' in out else ('40362风控' if jc and jc.group(1) == '40362' else '其他'))
    print('  %-22s %-10s code=%-6s => %s' % (tag, out, jc.group(1) if jc else '-', kind))

M1, H1 = fetch_challenge()
print('挑战#1: meta=%d build=%s' % (len(M1), H1[:16]))
ck1 = gen(M1, H1)

M2, H2 = fetch_challenge()
print('挑战#2: meta=%d build=%s   (M1==M2 ? %s)' % (len(M2), H2[:16], M1 == M2))

print('\n请求：')
probe('ck(M1) + M1', ck1, M1)
probe('ck(M1) + M2', ck1, M2)
probe('ck(M1) + M1(再测)', ck1, M1)
