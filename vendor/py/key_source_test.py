# -*- coding: utf-8 -*-
"""判定：ck 的密钥是否依赖「每请求的 build（wasm 数据段）」。
方法：抓一个挑战得到 meta M + hash H1；分别用 build H1 和另一个 build H2 配同一个 M 生成 ck，
      再分别请求。若两者都 40362 ⇒ 密钥只由 meta 决定（与 build 无关）；
      若 H2 那次掉回 694 ⇒ 密钥还依赖 build（wasm 数据段带每请求密钥材料）。"""
import subprocess, io, os, re, time, shutil, json

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"
OTHER = '.probe/oracle/live_08436d23.js'          # 另一个（旧）build

def curl(args): return subprocess.run(['curl', '-s'] + args, capture_output=True, text=True).stdout

# 1) 抓挑战
h = curl(['-H', 'User-Agent: ' + UA, ACT])
M = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', h).group(1)
H = re.search(r'zse-ck/v4/([0-9a-f]{64})\.js', h).group(1)
io.open('.probe/oracle/meta2.txt', 'w', encoding='utf-8').write(M)
bp = '.probe/oracle/builds/%s.js' % H[:16]
curl(['-o', bp, '-H', 'User-Agent: ' + UA, 'https://static.zhihu.com/zse-ck/v4/%s.js' % H])
print('挑战: meta=%d  build=%s  (%d bytes)' % (len(M), H[:16], os.path.getsize(bp)))

def gen(build, url, tag):
    env = dict(os.environ, BUILD=build, GTRACE='0', NOEVAL='1', WAIT_MS='9000', BUILD_URL=url)
    r = subprocess.run(['node', '.probe/zsec/run_build_live.js'], capture_output=True, text=True, env=env, timeout=200)
    ck = io.open('.probe/zsec/ck_build.txt', encoding='utf-8').read().strip()
    shutil.copy('.probe/zsec/ck_build.txt', '.probe/zsec/ck_pair_%s.txt' % tag)
    print('  [%s] 错误数=%s ck=%d' % (tag, (re.search(r'错误数: (\d+)', r.stdout) or ['','?'])[1], len(ck)))
    return ck

def probe(tag, ck, meta):
    time.sleep(6)
    out = subprocess.run(['curl', '-s', '-o', '.probe/oracle/ctrl/pair.body', '-w', '%{http_code} %{size_download}',
                          '-H', 'User-Agent: ' + UA, '-H', 'Cookie: __zse_ck=' + ck + '-' + meta, ACT],
                         capture_output=True, text=True).stdout.strip()
    body = io.open('.probe/oracle/ctrl/pair.body', 'rb').read().decode('utf-8', 'replace')
    jc = re.search(r'"code"\s*:\s*(\d+)', body)
    kind = '302通过' if out.startswith('302') else ('694挑战页' if '694' in out else ('40362风控' if jc and jc.group(1) == '40362' else '其他'))
    print('  [%s] %-10s code=%-6s => %s' % (tag, out, jc.group(1) if jc else '-', kind))

print('\n用「同一次挑战的 meta M」配两个不同 build 各生成一次 ck：')
ck1 = gen(bp, 'https://static.zhihu.com/zse-ck/v4/%s.js' % H, 'buildH1')
ck2 = gen(OTHER, 'https://static.zhihu.com/zse-ck/v4/%s.js' % H, 'buildH2')
print('\n请求（都用 meta M）：')
probe('ck(H1)+M', ck1, M)
probe('ck(H2)+M', ck2, M)
