# -*- coding: utf-8 -*-
"""下载多个不同 hash 的 build 并差分：找出「逐请求变化」的那一段。"""
import subprocess, io, os, json, re, hashlib

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
OUT = '.probe/oracle/builds'
os.makedirs(OUT, exist_ok=True)

runs = json.load(io.open('.probe/oracle/hash_meta_runs.json', encoding='utf-8'))
hashes = []
for r in runs:
    if r['build'] and r['build'] not in hashes:
        hashes.append(r['build'])
print('候选 hash:', len(hashes))

files = []
for h in hashes[:4]:
    p = os.path.join(OUT, h[:16] + '.js')
    if not os.path.exists(p):
        url = 'https://static.zhihu.com/zse-ck/v4/%s.js' % h
        subprocess.run(['curl', '-s', '-o', p, '-H', 'User-Agent: ' + UA, url], capture_output=True)
    if os.path.exists(p):
        d = io.open(p, 'rb').read()
        files.append((h, p, d))
        print('%-18s %8d bytes  sha256=%s' % (h[:16], len(d), hashlib.sha256(d).hexdigest()[:16]))

print('\n=== 长度对比 ===')
for h, p, d in files:
    print('  %s  %d' % (h[:16], len(d)))

if len(files) >= 2:
    a = files[0][2]; b = files[1][2]
    n = min(len(a), len(b))
    # 找第一处差异和最后一处差异
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    j = 0
    while j < n - i and a[len(a) - 1 - j] == b[len(b) - 1 - j]:
        j += 1
    print('\n=== 第一处差异 @%d / 末尾共同 %d 字节 ===' % (i, j))
    print('A 片段:', a[max(0, i-60):i+140].decode('utf-8', 'replace'))
    print('B 片段:', b[max(0, i-60):i+140].decode('utf-8', 'replace'))
    # 统计差异字节数
    diff = sum(1 for k in range(n) if a[k] != b[k])
    print('\n差异字节数(共同前缀后): %d / %d (%.2f%%)' % (diff, n, 100.0 * diff / n))
    # 逐 100KB 分块的差异密度
    print('\n=== 分块差异密度（每 50KB）===')
    for s in range(0, n, 50000):
        e = min(n, s + 50000)
        dd = sum(1 for k in range(s, e) if a[k] != b[k])
        if dd:
            print('  [%7d,%7d)  diff=%6d  (%.1f%%)' % (s, e, dd, 100.0 * dd / (e - s)))
