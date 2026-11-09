# -*- coding: utf-8 -*-
"""逐字段变异探针：拿一次真实挑战 → 跑我的环境出 ck+内存 → extract 出会话参数
→ 用 gen 对 record93 做受控变异 → 逐个提交，看服务端对每个字段的实际反应。

目的：判定 40362 到底卡在 record93 的哪一段（位图 / 指纹 / crc32(UA) / tail4），
而不是继续按「守卫→位」的静态推断猜。
"""
import subprocess, io, re, os, sys, json, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import zse_ck_tool as T

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def curl(args):
    r = subprocess.run(['curl', '-s'] + args, capture_output=True, text=True)
    return r.stdout


def submit(ck, meta):
    out = curl(['-o', os.path.join(ROOT, '.probe/oracle/ctrl/mut.body'), '-w', '%{http_code}',
                '-H', 'User-Agent: ' + UA,
                '-H', 'Cookie: __zse_ck=' + ck + '-' + meta, ACT])
    try:
        body = io.open(os.path.join(ROOT, '.probe/oracle/ctrl/mut.body'), 'rb').read().decode('utf-8', 'replace')
    except Exception:
        body = ''
    jc = re.search(r'"code"\s*:\s*(\d+)', body)
    return out.strip(), (jc.group(1) if jc else None)


def main():
    t0 = time.time()
    h = curl(['-H', 'User-Agent: ' + UA, ACT])
    m = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', h)
    b = re.search(r'zse-ck/v4/([0-9a-f]{64})\.js', h)
    if not m or not b:
        print('挑战页异常 len=%d' % len(h)); return
    meta, bh = m.group(1), b.group(1)
    print('[%5.1fs] 挑战 meta=%d chars build=%s' % (time.time() - t0, len(meta), bh[:16]))

    bp = os.path.join(ROOT, '.probe/oracle/live_mut.js')
    curl(['-o', bp, '-H', 'User-Agent: ' + UA,
          'https://static.zhihu.com/zse-ck/v4/%s.js' % bh])

    env = dict(os.environ, BUILD=bp, GTRACE='0', NOEVAL='1',
               BUILD_URL='https://static.zhihu.com/zse-ck/v4/%s.js' % bh, WAIT_MS='9000')
    r = subprocess.run(['node', os.path.join(ROOT, '.probe/zsec/run_build_live.js')],
                       capture_output=True, text=True, env=env, timeout=180, cwd=ROOT)
    err = re.search(r'错误数: (\d+)', r.stdout)
    ck = io.open(os.path.join(ROOT, '.probe/zsec/ck_build.txt'), encoding='utf-8').read().strip()
    print('[%5.1fs] 跑完 错误数=%s ck=%d' % (time.time() - t0, err.group(1) if err else '?', len(ck)))

    prof = T.extract(meta, os.path.join(ROOT, '.probe/zsec/mem_build.bin'))
    rec = prof['rec93']
    print('  rec93 = %s' % rec)
    print('  位图  = %s' % rec[:48])
    ok = (T.generate_token(prof, rec) == ck)
    print('  自检 gen(profile,rec93) == 抓到的 ck : %s' % ok)

    real = '36c020001000800040308000800088008000800080004000'
    print('\n  === 基线 ===')
    c, code = submit(ck, meta)
    print('  %-26s -> %s code=%s' % ('基线(我的位图)', c, code))

    def trial(name, newrec):
        t = T.generate_token(prof, newrec)
        c, code = submit(t, meta)
        print('  %-26s -> %s code=%s' % (name, c, code))

    print('\n  === 位图变异 ===')
    trial('位图=真样本值', real + rec[48:])
    trial('位图全 0', '0' * 48 + rec[48:])
    trial('位图全 f', 'f' * 48 + rec[48:])
    trial('位图=真值低16位清零', real[:44] + '0000' + rec[48:])
    trial('位图=我的(显式重放)', rec[:48] + rec[48:])

    print('\n  === 其余字段变异 ===')
    trial('指纹 32hex 清零', rec[:48] + '0' * 32 + rec[80:])
    trial('指纹 32hex 翻转末位', rec[:48] + rec[48:79] + ('0' if rec[79] != '0' else '1') + rec[80:])
    trial('crc32(UA) 清零', rec[:81] + '0' * 8 + rec[89:])
    trial('tail4 清零', rec[:89] + '0000')
    trial('tail4 递增', rec[:89] + '%04x' % ((int(rec[89:], 16) + 1) & 0xFFFF))
    trial('z 分隔符改 y', rec[:80] + 'y' + rec[81:])
    print('[%5.1fs] 完成' % (time.time() - t0))


if __name__ == '__main__':
    main()
