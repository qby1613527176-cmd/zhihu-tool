# -*- coding: utf-8 -*-
"""干净闭环：curl 直连真站（不经任何 mock/代理），节制节奏避免风控。
每步都把响应体分类：风控(118B JSON) / 挑战页(694B HTML 带 meta) / 成功(200 真内容)。
"""
import subprocess, io, re, os, sys, time, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"
BODY = os.path.join(ROOT, '.probe/oracle/ctrl/lc.body')


def curl(args):
    return subprocess.run(['curl', '-s'] + args, capture_output=True, text=True).stdout


def classify(body):
    if '"code":40362' in body or '暂时限制本次访问' in body:
        return '风控'
    if 'zh-zse-ck' in body:
        return '挑战页'
    return '成功'


def get(u, cookie=None):
    a = ['-o', BODY, '-w', '%{http_code}', '-H', 'User-Agent: ' + UA, u]
    if cookie:
        a = a[:3] + ['-H', 'Cookie: __zse_ck=' + cookie] + a[3:]
    http = curl(a).strip()
    body = io.open(BODY, 'rb').read().decode('utf-8', 'replace')
    return http, classify(body), body


def main():
    t0 = time.time()
    print('[%5.1fs] 1) 取挑战' % (time.time() - t0))
    http, kind, body = get(ACT)
    m = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', body)
    b = re.search(r'zse-ck/v4/([0-9a-f]{64})\.js', body)
    print('     HTTP=%s 类型=%s len=%d meta=%s build=%s' % (http, kind, len(body),
          (len(m.group(1)) if m else None), (b.group(1)[:16] if b else None)))
    if not m or not b:
        print('     拿不到挑战，退出'); return
    meta, bh = m.group(1), b.group(1)

    bp = os.path.join(ROOT, '.probe/oracle/live_clean.js')
    curl(['-o', bp, '-H', 'User-Agent: ' + UA,
          'https://static.zhihu.com/zse-ck/v4/%s.js' % bh])
    print('[%5.1fs] 2) build 下载 %d bytes' % (time.time() - t0, os.path.getsize(bp)))

    env = dict(os.environ, BUILD=bp, GTRACE='0', NOEVAL='1',
               BUILD_URL='https://static.zhihu.com/zse-ck/v4/%s.js' % bh, WAIT_MS='9000')
    r = subprocess.run(['node', os.path.join(ROOT, '.probe/zsec/run_build_live.js')],
                       capture_output=True, text=True, env=env, timeout=180, cwd=ROOT)
    err = re.search(r'错误数: (\d+)', r.stdout)
    ck = io.open(os.path.join(ROOT, '.probe/zsec/ck_build.txt'), encoding='utf-8').read().strip()
    print('[%5.1fs] 3) 我的环境跑完 错误数=%s ck=%d chars %s' % (
        time.time() - t0, err.group(1) if err else '?', len(ck), ck[:28]))

    time.sleep(6)
    print('[%5.1fs] 4) 提交 ck+meta（本会话）' % (time.time() - t0))
    http, kind, body = get(ACT, ck + '-' + meta)
    print('     HTTP=%s 类型=%s len=%d' % (http, kind, len(body)))
    io.open(os.path.join(ROOT, '.probe/oracle/ctrl/lc_1.body'), 'w', encoding='utf-8').write(body)

    time.sleep(6)
    print('[%5.1fs] 5) 再取一次挑战（拿第二份 meta）' % (time.time() - t0))
    http2, kind2, body2 = get(ACT)
    m2 = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', body2)
    print('     HTTP=%s 类型=%s len=%d 同meta=%s' % (http2, kind2, len(body2),
          (m2.group(1) == meta) if m2 else None))

    if m2:
        time.sleep(6)
        print('[%5.1fs] 6) 提交 本会话ck + 另一份 meta' % (time.time() - t0))
        http3, kind3, body3 = get(ACT, ck + '-' + m2.group(1))
        print('     HTTP=%s 类型=%s len=%d' % (http3, kind3, len(body3)))
        io.open(os.path.join(ROOT, '.probe/oracle/ctrl/lc_2.body'), 'w', encoding='utf-8').write(body3)

    time.sleep(6)
    print('[%5.1fs] 7) 提交 ck（不带 meta）' % (time.time() - t0))
    http4, kind4, body4 = get(ACT, ck)
    print('     HTTP=%s 类型=%s len=%d' % (http4, kind4, len(body4)))
    print('[%5.1fs] 完成' % (time.time() - t0))


if __name__ == '__main__':
    main()
