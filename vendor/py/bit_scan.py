# -*- coding: utf-8 -*-
"""逐位扫描：拿捕获的真实 profile/record93 作基线，**每次只置一个我多出来的位**，
提交看服务端是否报 40362。用来判定服务端到底校验位图的哪几位（以及 bit3 是否致命）。"""
import subprocess, io, re, os, sys, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import zse_ck_tool as T

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"

# (word 下标, bit, 我这边该位是 1 而真机是 0)
EXTRA = [(1, 3), (1, 1), (1, 0), (4, 13), (4, 11), (8, 11), (8, 9), (10, 14)]


def curl(a):
    return subprocess.run(['curl', '-s'] + a, capture_output=True, text=True).stdout


def submit(ck, meta):
    curl(['-o', os.path.join(ROOT, '.probe/oracle/ctrl/bs.body'), '-w', '%{http_code}',
          '-H', 'User-Agent: ' + UA, '-H', 'Cookie: __zse_ck=' + ck + '-' + meta, ACT])
    body = io.open(os.path.join(ROOT, '.probe/oracle/ctrl/bs.body'), 'rb').read().decode('utf-8', 'replace')
    jc = re.search(r'"code"\s*:\s*(\d+)', body)
    return (jc.group(1) if jc else 'soft'), ('chal' if '<meta id="zh-zse-ck"' in body else 'nochal')


def main():
    d = json.load(io.open(os.path.join(ROOT, '.probe/vmsvc/env_samples.json'), encoding='utf-8'))
    s = d[0]
    prof = dict(rk=s['rk'], mask=s['mask'], iv=s['iv'], xreg=s['xreg'], alphabet=s['alphabet'])
    rec = s['rec93']
    assert T.generate_token(prof, rec) == s['token']
    print('gen 自检 OK；基线位图 = %s' % rec[:48])

    h = curl(['-H', 'User-Agent: ' + UA, ACT])
    meta = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', h).group(1)
    print('新鲜 meta = %d chars\n' % len(meta))

    def run(name, rec2):
        ck = T.generate_token(prof, rec2)
        code, ch = submit(ck, meta)
        print('  %-30s code=%-8s %s' % (name, code, ch))
        return code

    run('基线（真机位图，未改）', rec)
    print()
    for w, b in EXTRA:
        words = [rec[i:i + 4] for i in range(0, 48, 4)]
        v = int(words[w], 16) | (1 << b)
        words[w] = '%04x' % v
        run('只置 w%d bit%-2d -> %s' % (w, b, words[w]), ''.join(words) + rec[48:])


if __name__ == '__main__':
    main()
