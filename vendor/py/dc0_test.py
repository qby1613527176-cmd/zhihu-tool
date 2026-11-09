# -*- coding: utf-8 -*-
"""d_c0 假设测试：
  真 ck 不带/带 d_c0 ；我的 ck 不带/带 d_c0 / 带整套真 cookie。
若「我的 ck + d_c0」变成 302 ⇒ 40362 是缺 d_c0 造成的；若仍 40362 ⇒ 与 d_c0 无关。"""
import subprocess, io, os, re, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"

D_C0  = 'lscZDG__BB2PTlvTIUo3ZA8lA2lbBE-ncKM=|1791331602'
XSRF  = 'pBc4PrWxpXSTRybcMppcajpqafqewvgM'
ZAP   = 'b15b6cdd-3e2e-4b40-83e8-e9738191eb3f'

REAL_CK = ("005_rebdvSrc60HFQmZymBS2DH5yUSyahj9vuyEEoMNLYsOnetshd7tug1d86KuUvKvBDb522XvyuaVnKg8a4pIeNH4pIygki"
           "M4xPOviIqHtNJ7cQb1pxaWcocSlzrnaKVVx")
REAL_META = ("9tOv1xOxhFl+tw8Y+aaEnTJqDrFdcB5zKCCn+f4xqCHjXMhyd0LnfZ8evkcZ1qzTluOVTC9oOk71WuShwgbYS7f3fUWcbHc3CG+3"
             "/XapdzcDtLQI53hb1ZBPZJ1ayU7E+Z6Nn70jVMpWC9vC8uxGn7sy2zhYvn2rI/FTSDdhoVs=")

def curl(args): return subprocess.run(['curl', '-s'] + args, capture_output=True, text=True).stdout

# 拿一个新鲜 meta + 生成我的 ck
h = curl(['-H', 'User-Agent: ' + UA, ACT])
M = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', h).group(1)
H = re.search(r'zse-ck/v4/([0-9a-f]{64})\.js', h).group(1)
io.open('.probe/oracle/meta2.txt', 'w', encoding='utf-8').write(M)
bp = '.probe/oracle/builds/%s.js' % H[:16]
curl(['-o', bp, '-H', 'User-Agent: ' + UA, 'https://static.zhihu.com/zse-ck/v4/%s.js' % H])
env = dict(os.environ, BUILD=bp, GTRACE='0', NOEVAL='1', WAIT_MS='9000',
           BUILD_URL='https://static.zhihu.com/zse-ck/v4/%s.js' % H)
r = subprocess.run(['node', '.probe/zsec/run_build_live.js'], capture_output=True, text=True, env=env, timeout=220)
MY_CK = io.open('.probe/zsec/ck_build.txt', encoding='utf-8').read().strip()
print('新 meta=%d  build=%s  我的 ck=%d  错误数=%s' % (
    len(M), H[:16], len(MY_CK), (re.search(r'错误数: (\d+)', r.stdout) or ['', '?'])[1]))

def probe(tag, cookie):
    time.sleep(6)
    out = subprocess.run(['curl', '-s', '-o', '.probe/oracle/ctrl/dc0.body', '-w', '%{http_code} %{size_download}',
                          '-H', 'User-Agent: ' + UA, '-H', 'Cookie: ' + cookie, ACT],
                         capture_output=True, text=True).stdout.strip()
    body = io.open('.probe/oracle/ctrl/dc0.body', 'rb').read().decode('utf-8', 'replace')
    jc = re.search(r'"code"\s*:\s*(\d+)', body)
    kind = '302通过' if out.startswith('302') else ('694挑战页' if '694' in out else ('40362风控' if jc and jc.group(1) == '40362' else '其他'))
    print('  %-30s %-10s code=%-6s => %s' % (tag, out, jc.group(1) if jc else '-', kind))

print('\n请求：')
probe('真ck+真meta（不带 d_c0）',      '__zse_ck=' + REAL_CK)
probe('真ck+真meta + d_c0',            '__zse_ck=%s; d_c0=%s' % (REAL_CK, D_C0))
probe('我的ck+我的meta（不带 d_c0）',  '__zse_ck=%s-%s' % (MY_CK, M))
probe('我的ck+我的meta + d_c0',        '__zse_ck=%s-%s; d_c0=%s' % (MY_CK, M, D_C0))
probe('我的ck+我的meta + 全套真cookie', '__zse_ck=%s-%s; d_c0=%s; _xsrf=%s; _zap=%s' % (MY_CK, M, D_C0, XSRF, ZAP))
