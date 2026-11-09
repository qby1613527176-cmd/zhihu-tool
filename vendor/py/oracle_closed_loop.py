# -*- coding: utf-8 -*-
"""闭环实验：抓挑战 → 立刻下载该 build → 立刻跑出 ck → 立刻用 ck+meta 请求。
目的：区分 40362 是「挑战过期」还是「环境指纹不符」。"""
import subprocess, io, re, os, time, json, sys

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"

def curl(args):
    r = subprocess.run(['curl', '-s'] + args, capture_output=True, text=True)
    return r.stdout

t0 = time.time()
# 1) 抓挑战
h = curl(['-H', 'User-Agent: ' + UA, ACT])
m = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', h)
b = re.search(r'zse-ck/v4/([0-9a-f]{64})\.js', h)
if not m or not b:
    print('挑战页异常 len=%d' % len(h)); sys.exit(1)
meta, bh = m.group(1), b.group(1)
io.open('.probe/oracle/meta2.txt', 'w', encoding='utf-8').write(meta)
print('[%5.1fs] 挑战: meta=%d chars  build=%s' % (time.time()-t0, len(meta), bh[:16]))

# 2) 立刻下载该 build
bp = '.probe/oracle/live_fresh.js'
curl(['-o', bp, '-H', 'User-Agent: ' + UA, 'https://static.zhihu.com/zse-ck/v4/%s.js' % bh])
print('[%5.1fs] build 下载: %d bytes' % (time.time()-t0, os.path.getsize(bp)))

# 3) 立刻跑出 ck
env = dict(os.environ, BUILD=bp, GTRACE='0', NOEVAL='1',
           BUILD_URL='https://static.zhihu.com/zse-ck/v4/%s.js' % bh, WAIT_MS='9000')
r = subprocess.run(['node', '.probe/zsec/run_build_live.js'], capture_output=True, text=True, env=env, timeout=120)
err = re.search(r'错误数: (\d+)', r.stdout)
ck = io.open('.probe/zsec/ck_build.txt', encoding='utf-8').read().strip() if os.path.exists('.probe/zsec/ck_build.txt') else ''
print('[%5.1fs] 跑完: 错误数=%s  ck=%d chars %s' % (time.time()-t0, err.group(1) if err else '?', len(ck), ck[:24]))

# 4) 立刻请求（ck+meta 与 ck 单发各一次）
for tag, cookie in [('ck+meta', ck + '-' + meta), ('ck_only', ck)]:
    out = curl(['-o', '.probe/oracle/ctrl/final_%s.body' % tag, '-w', '%{http_code} %{size_download}',
                '-H', 'User-Agent: ' + UA,
                '-H', 'Cookie: __zse_ck=' + cookie, ACT])
    body = io.open('.probe/oracle/ctrl/final_%s.body' % tag, 'rb').read().decode('utf-8', 'replace')
    jc = re.search(r'"code"\s*:\s*(\d+)', body)
    print('[%5.1fs] %-8s -> %-10s code=%-6s challenge=%s' % (
        time.time()-t0, tag, out.strip(), jc.group(1) if jc else None,
        '<meta id="zh-zse-ck"' in body))
