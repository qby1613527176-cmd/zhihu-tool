# -*- coding: utf-8 -*-
"""用真实浏览器拿到的有效 (ck, meta) 做受控对照：
  A) 真实 ck + 它自己的 meta   → 预期通过（无挑战页）
  B) 真实 ck + 另一份 meta     → 预期 403 + 挑战页
  C) 我的环境 ck + 真实 meta   → 预期 403 + 挑战页
每步间隔 8s，避免触发风控；响应按体分类。
"""
import subprocess, io, re, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"
BODY = os.path.join(ROOT, '.probe/oracle/ctrl/mb.body')

REAL = ("005_cwasRwkC0YlR=qUPeykiLDzJx4a/PdUDxwuIPmKEv7Q8wodEJivlUIElCpjheG2jhmi/vHGYcXsPe0aP6HdoHbpvqhw9KJvdeqWS6Eq/utkOyGQHYZHKy6A817roBXgg"
        "-P4FozKUPFgKlfX/P06UeOnMTqKJDYeSG3wUbJGmme3XA8Nkii4AcpbTHU/yJN8JXOcjgXcp47ewYXejQS4viZEAjGTxqVHspZO3TOYDkoSwoLJGRE65af6umDQKOjGd00EU7whTDBshTMvQD9Ow1TWncHxGuOXsTXJ+sP+TKUIg=")


def get(cookie=None, path=ACT):
    a = ['-s', '-o', BODY, '-w', '%{http_code}',
         '-H', 'User-Agent: ' + UA]
    if cookie is not None:
        a += ['-H', 'Cookie: __zse_ck=' + cookie]
    a += [path]
    http = subprocess.run(['curl'] + a, capture_output=True, text=True).stdout.strip()
    body = io.open(BODY, 'rb').read().decode('utf-8', 'replace')
    if '暂时限制本次访问' in body or '"code":40362' in body:
        kind = '风控'
    elif 'zh-zse-ck' in body:
        kind = '挑战页'
    else:
        kind = '通过'
    return http, kind, body


def main():
    t0 = time.time()
    ck, meta = REAL[:132], REAL[133:]
    print('真实 ck = %s… (%d)' % (ck[:30], len(ck)))
    print('真实 meta = %s… (%d)' % (meta[:24], len(meta)))
    assert REAL[132] == '-', '分隔符不是 -'

    print('\n[A] 真实 ck + 它自己的 meta')
    h, k, b = get(ck + '-' + meta)
    print('    HTTP=%s 类型=%s len=%d 标题=%s' % (h, k, len(b),
          (re.search(r'<title>([^<]*)</title>', b) or ['', '?'])[1]))

    time.sleep(8)
    print('\n[B] 取一份别的 meta（无 cookie 请求挑战页）')
    h2, k2, b2 = get(None)
    m2 = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', b2)
    meta2 = m2.group(1) if m2 else None
    print('    HTTP=%s 类型=%s len=%d 与真实 meta 相同=%s' % (h2, k2, len(b2), meta2 == meta))

    if meta2:
        time.sleep(8)
        print('\n[B2] 真实 ck + 另一份 meta')
        h3, k3, b3 = get(ck + '-' + meta2)
        print('    HTTP=%s 类型=%s len=%d' % (h3, k3, len(b3)))
        io.open(os.path.join(ROOT, '.probe/oracle/ctrl/mb_B.body'), 'w', encoding='utf-8').write(b3)

    mine = io.open(os.path.join(ROOT, '.probe/zsec/ck_build.txt'), encoding='utf-8').read().strip()
    time.sleep(8)
    print('\n[C] 我的环境 ck + 真实 meta')
    print('    我的 ck = %s… (%d)' % (mine[:30], len(mine)))
    h4, k4, b4 = get(mine + '-' + meta)
    print('    HTTP=%s 类型=%s len=%d' % (h4, k4, len(b4)))
    io.open(os.path.join(ROOT, '.probe/oracle/ctrl/mb_C.body'), 'w', encoding='utf-8').write(b4)

    time.sleep(8)
    print('\n[D] 真实 ck + 它自己的 meta（复测，确认可重复）')
    h5, k5, b5 = get(ck + '-' + meta)
    print('    HTTP=%s 类型=%s len=%d' % (h5, k5, len(b5)))
    print('\n[%5.1fs] 完成' % (time.time() - t0))


if __name__ == '__main__':
    main()
