# -*- coding: utf-8 -*-
"""多轮抓挑战页：记录 (http, size, meta, build hash, 关键响应头)。带间隔避免自测流量触发风控。"""
import subprocess, re, time, io, os, json, sys

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
URL = "https://www.zhihu.com/people/zhihuadmin/activities"
OUT = '.probe/oracle/probe_runs'
os.makedirs(OUT, exist_ok=True)

def fetch(tag, cookie=None, sleep_before=0):
    if sleep_before:
        time.sleep(sleep_before)
    hf = os.path.join(OUT, tag + '.headers')
    bf = os.path.join(OUT, tag + '.body')
    cmd = ['curl', '-s', '-D', hf, '-o', bf, '-w', '%{http_code} %{size_download}',
           '-H', 'User-Agent: ' + UA,
           '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
           '-H', 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8']
    if cookie:
        cmd += ['-H', 'Cookie: __zse_ck=' + cookie]
    cmd.append(URL)
    r = subprocess.run(cmd, capture_output=True, text=True)
    code_size = r.stdout.strip()
    hdr = io.open(hf, encoding='utf-8', errors='replace').read()
    body = io.open(bf, 'rb').read()
    txt = body.decode('utf-8', 'replace')
    meta = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', txt)
    build = re.search(r'zse-ck/v4/([0-9a-f]{64})\.js', txt)
    row = {
        'tag': tag, 'code_size': code_size,
        'meta': meta.group(1) if meta else None,
        'meta_len': len(meta.group(1)) if meta else 0,
        'build': build.group(1) if build else None,
        'body_head': txt[:120].replace('\n', ' '),
        'set_cookie': [l.strip() for l in hdr.split('\n') if l.lower().startswith('set-cookie')][:3],
        'x_log': [l.strip() for l in hdr.split('\n') if 'x-' in l.lower()][:6],
    }
    return row

rows = []
# 0) 无 cookie 基线
rows.append(fetch('base_nocookie'))
for i in range(1, 6):
    rows.append(fetch('run%d' % i, sleep_before=4))

print(json.dumps(rows, ensure_ascii=False, indent=1))
io.open('.probe/oracle/hash_meta_runs.json', 'w', encoding='utf-8').write(
    json.dumps(rows, ensure_ascii=False, indent=1))

print('\n=== 汇总 ===')
for r in rows:
    print('%-14s %-12s meta=%-5s build=%s' % (r['tag'], r['code_size'],
          r['meta_len'], (r['build'] or '')[:16]))
metas = [r['meta'] for r in rows if r['meta']]
builds = [r['build'] for r in rows if r['build']]
print('distinct metas :', len(set(metas)), '/', len(metas))
print('distinct builds:', len(set(builds)), '/', len(builds))
