# -*- coding: utf-8 -*-
"""同 meta 连跑两次：判定 ck 是否确定（有无随机 nonce）。
再跑一次改动环境（UA）的：判定 ck 是否随环境变（即一个 meta 能否对应多个 ck）。"""
import subprocess, io, os, re, hashlib, shutil

META = io.open('.probe/oracle/meta2.txt', encoding='utf-8').read().strip()
BUILD = '.probe/oracle/live_08436d23.js'
URL = 'https://static.zhihu.com/zse-ck/v4/471780e949f4d052fe1e6ef6b41e03e754bb2e1f9eba278f3b2f224a2b8f1cb9.js'

def run(tag, extra_env=None):
    env = dict(os.environ, BUILD=BUILD, GTRACE='0', NOEVAL='1', WAIT_MS='9000', BUILD_URL=URL)
    if extra_env: env.update(extra_env)
    r = subprocess.run(['node', '.probe/zsec/run_build_live.js'], capture_output=True, text=True, env=env, timeout=200)
    err = re.search(r'错误数: (\d+)', r.stdout)
    ck = io.open('.probe/zsec/ck_build.txt', encoding='utf-8').read().strip()
    qh = re.search(r'"qh":"(s\d+:[^"]{0,50})', r.stdout)
    dst = '.probe/zsec/ck_%s.txt' % tag
    shutil.copy('.probe/zsec/ck_build.txt', dst)
    print('%-10s 错误数=%s  ck len=%d  sha=%s  head=%s' % (
        tag, err.group(1) if err else '?', len(ck), hashlib.sha256(ck.encode()).hexdigest()[:12], ck[:26]))
    return ck

a = run('run1')
b = run('run2')
print('\n同 meta 同环境两次: %s' % ('IDENTICAL（无随机量）' if a == b else 'DIFFER（ck 含随机量/时间量）'))
print('差异位置:', [i for i in range(min(len(a), len(b))) if a[i] != b[i]][:12])

print('\n--- 改动环境（UA）后再跑 ---')
c = run('run3_ua', {'ZSE_UA': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) Gecko/20100101 Firefox/152.0'})
print('改 UA 后与 run1: %s' % ('SAME（ck 与 UA 无关）' if a == c else 'DIFFER（ck 随环境变）'))
