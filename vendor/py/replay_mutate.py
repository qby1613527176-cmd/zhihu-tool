# -*- coding: utf-8 -*-
"""用捕获的真实会话 profile 做服务端回放 + record93 字段变异，
直接观察服务端对「位图 / 指纹 / crc32 / tail4」各段的实际反应。"""
import subprocess, io, re, os, sys, json, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import zse_ck_tool as T

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"


def curl(args):
    return subprocess.run(['curl', '-s'] + args, capture_output=True, text=True).stdout


def submit(ck, meta):
    http = curl(['-o', os.path.join(ROOT, '.probe/oracle/ctrl/rm.body'), '-w', '%{http_code}',
                 '-H', 'User-Agent: ' + UA, '-H', 'Cookie: __zse_ck=' + ck + '-' + meta, ACT]).strip()
    try:
        body = io.open(os.path.join(ROOT, '.probe/oracle/ctrl/rm.body'), 'rb').read().decode('utf-8', 'replace')
    except Exception:
        body = ''
    jc = re.search(r'"code"\s*:\s*(\d+)', body)
    return http, (jc.group(1) if jc else None), ('challenge' if '<meta id="zh-zse-ck"' in body else 'no-chal'), len(body)


def main():
    d = json.load(io.open(os.path.join(ROOT, '.probe/vmsvc/env_samples.json'), encoding='utf-8'))
    s = d[0]
    prof = dict(rk=s['rk'], mask=s['mask'], iv=s['iv'], xreg=s['xreg'], alphabet=s['alphabet'])
    rec = s['rec93']
    tok = s['token']
    assert T.generate_token(prof, rec) == tok, 'gen 自检失败'
    print('gen 自检 == 捕获 token : OK')

    h = curl(['-H', 'User-Agent: ' + UA, ACT])
    m = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', h)
    if not m:
        print('拿不到挑战 meta'); return
    meta = m.group(1)
    print('新鲜 meta = %d chars\n' % len(meta))

    def trial(name, ck):
        code, cm = submit(ck, meta)
        print('  %-28s code=%-7s %s' % (name, code, cm))

    print('=== 基线：捕获的真实 ck + 新鲜 meta（会话不同）===')
    trial('真实 token + 新 meta', tok)
    print('\n=== 用该 profile 重算同一 rec93（应等于真实 token）===')
    trial('gen(prof, 原 rec93)', T.generate_token(prof, rec))
    print('\n=== 位图变异 ===')
    trial('位图全 0', T.generate_token(prof, '0' * 48 + rec[48:]))
    trial('位图全 f', T.generate_token(prof, 'f' * 48 + rec[48:]))
    trial('位图 w1 改 200b(我的)', T.generate_token(prof, rec[:4] + '200b' + rec[8:]))
    print('\n=== 其余字段变异 ===')
    trial('指纹 32hex 清零', T.generate_token(prof, rec[:48] + '0' * 32 + rec[80:]))
    trial('crc32 清零', T.generate_token(prof, rec[:81] + '0' * 8 + rec[89:]))
    trial('tail4 清零', T.generate_token(prof, rec[:89] + '0000'))
    trial('tail4 +1', T.generate_token(prof, rec[:89] + '%04x' % ((int(rec[89:], 16) + 1) & 0xFFFF)))
    trial('z->y', T.generate_token(prof, rec[:80] + 'y' + rec[81:]))
    print('\n=== 对照：乱码 ck ===')
    trial('全 A 的 ck', '005_' + 'A' * 128)
    trial('空 ck', '')


if __name__ == '__main__':
    main()
