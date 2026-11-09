# -*- coding: utf-8 -*-
"""用已保存的 forge_profile.json 做干净逐位扫描：从「我的位图」出发，每次只清一个位，
看哪一位清掉后提交从 403 变 302。间隔 10s 排风控。"""
import subprocess, io, re, os, sys, time, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import zse_ck_tool as T

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"
BODY = os.path.join(ROOT, '.probe/oracle/ctrl/bs2.body')
TARGET_W = ['2000', '1000', '8000', '4030', '8000', '8000', '8800', '8000', '8000', '8000', '4000']
EXTRA = [(1, 3), (1, 1), (1, 0), (4, 13), (4, 11), (8, 11), (8, 9), (10, 14)]


def get(cookie):
    http = subprocess.run(['curl', '-s', '-o', BODY, '-w', '%{http_code}', '-H', 'User-Agent: ' + UA,
                           '-H', 'Cookie: __zse_ck=' + cookie, ACT],
                          capture_output=True, text=True).stdout.strip()
    b = io.open(BODY, 'rb').read().decode('utf-8', 'replace')
    if '暂时限制本次访问' in b or '"code":40362' in b:
        return http, '40362风控'
    if 'zh-zse-ck' in b:
        return http, '403挑战页'
    return http, '302通过'


def main():
    p = json.load(io.open(os.path.join(ROOT, '.probe/zsec/forge_profile.json'), encoding='utf-8'))
    rk = p['rk']; mask = p['mask']; iv = bytes(p['iv'], 'latin1')
    XR = p['xreg']; alpha = p['alphabet']; rec = p['rec93']; meta = p['meta']
    pot = json.load(io.open(os.path.join(ROOT, '.probe/vmsvc/sbox_full.json')))['pot']
    sbox = [0] * 256
    for v, q in enumerate(pot):
        sbox[q] = v

    def gen(r):
        pt = r.encode() + bytes([3, 3, 3])
        return '005_' + T.radix64_encode(T.encrypt_96(pt, rk, iv, mask, sbox), alpha, XR)

    def test(tag, r):
        time.sleep(10)
        http, kind = get(gen(r) + '-' + meta)
        print('  %-30s HTTP=%-4s %s' % (tag, http, kind), flush=True)
        return kind

    print('基线 record93 = %s' % rec)
    print('我的位图 = %s' % ' '.join(rec[i:i + 4] for i in range(0, 48, 4)))
    print('目标位图 = %s' % (' '.join([rec[:4]] + TARGET_W)))
    print()
    print('=== 基线 / 全量替换 ===')
    test('基线（我的位图）', rec)
    test('位图全换真机常量', rec[:4] + ''.join(TARGET_W) + rec[48:])
    print()
    print('=== 逐位：从我的位图出发只清一个位 ===')
    for w, b in EXTRA:
        words = [rec[i:i + 4] for i in range(0, 48, 4)]
        v = int(words[w], 16) & ~(1 << b)
        words[w] = '%04x' % v
        test('清 w%d bit%-2d -> %s' % (w, b, words[w]), ''.join(words) + rec[48:])


if __name__ == '__main__':
    main()
