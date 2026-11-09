# -*- coding: utf-8 -*-
"""fresh_ck.py —— 请求全新挑战并产出一个 ck（端到端）。

流程（全部实测确认）：
  1. GET 目标页 → 服务端返回 **403 挑战页**（约 650 B），内含
       <meta id="zh-zse-ck" content="…">        ← 本会话的 meta
       <script src="…/zse-ck/v4/<build>.js">    ← 本会话的 build（每请求轮换）
  2. 本地跑挑战（Node 驱动 + 手写环境）→ 得到 `__g.ck`（132 字符，"005_" + 128）
  3. 拼 cookie：`__zse_ck = <__g.ck> + '-' + <meta>`
  4. 带该 cookie 再请求 → 按状态分类

用法：
    python py/fresh_ck.py                # 抓 meta → 算 ck → 提交并报告
    python py/fresh_ck.py --no-submit    # 只算 ck，不提交（避免触发风控）
    python py/fresh_ck.py --url <URL>    # 换目标页（默认 zhihuadmin/activities）

状态分类（见 PITFALLS D4/D5）：
    302                  → 通过
    403 + body 含 zh-zse-ck → 挑战页（服务端当作"没有/无效 token"）
    403/其他 + code=40362   → **风控**（不是内容判据；要等一会再试）
"""
import argparse
import gzip
import io
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)

# 真机捕获会话的 UA（与 record93 里的 crc32(UA) 同源）
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) ZCode/3.14.4 Chrome/146.0.7680.80 Electron/41.0.3 Safari/537.36')
DEFAULT_URL = 'https://www.zhihu.com/people/zhihuadmin/activities'

META_RE = re.compile(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"')
BUILD_RE = re.compile(r'src="(https://static\.zhihu\.com/zse-ck/v4/[0-9a-f]+\.js)"')
CK_RE = re.compile(r'005_[A-Za-z0-9+/=]{128}')


def fetch(url, cookie=None, timeout=25, follow=True):
    """返回 (status, body_text, headers)。403 也当正常返回（挑战页就是 403）。
    follow=False 时不跟随重定向 —— 这样能看到 302 的 Location（判读关键）。"""
    hdr = {
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Accept-Encoding': 'gzip',
        'Referer': 'https://www.zhihu.com/',
    }
    if cookie:
        hdr['Cookie'] = cookie
    req = urllib.request.Request(url, headers=hdr)
    ctx = ssl.create_default_context()
    if follow:
        op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    else:
        class _NoRedir(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        op = urllib.request.build_opener(_NoRedir, urllib.request.HTTPSHandler(context=ctx))
    try:
        r = op.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        r = e
    raw = r.read()
    if (r.headers.get('Content-Encoding') or '') == 'gzip':
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return r.status, raw.decode('utf-8', 'replace'), _CIHeaders(r.headers)


class _CIHeaders(dict):
    """大小写不敏感的响应头。

    ⚠️ 不能用 `dict(r.headers)`：Python 的键**全小写** ⇒ `h.get('Location')` 恒为 None，
    `classify()` 就拿不到 Location，会把「302 → /account/unhuman」当成「通过」（见 docs/PITFALLS.md）。
    """
    def __init__(self, msg):
        super().__init__((k.lower(), v) for k, v in msg.items())

    def get(self, key, default=None):
        return super().get(key.lower(), default)

    def __contains__(self, key):
        return super().__contains__(key.lower() if isinstance(key, str) else key)


def classify(status, body, location=''):
    """响应有四种，按 status + body + Location 分类（实测补充，见 PITFALLS D4）。

      302 -> /account/unhuman   token 检查**通过**，但会话被反自动化层标记
      302 -> 其他               完全通过（拿到目标内容）
      403 + 含 zh-zse-ck        ck 被拒（服务端重发挑战页）
      403 + code=40362          风控限流（不是内容判据，需等待后重试）
    """
    if status in (301, 302, 303, 307, 308):
        location = (location or '').strip()
        if not location:
            return '302 但**没有 Location**（判读不了落点，必须看原始响应）'
        if '/account/unhuman' in location:
            return 'ck 通过（token 有效）；会话被反自动化层标记 → /account/unhuman'
        return '通过 → %s' % (location or '(无 Location)')
    m = re.search(r'"code"\s*:\s*(\d+)', body)
    code = m.group(1) if m else None
    if code == '40362':
        return '40362 风控（不是内容判据，需等待后重试）'
    if status == 403 and '<meta id="zh-zse-ck"' in body:
        return '403 挑战页（ck 被拒：服务端当作没有/无效 token）'
    if status == 200 and '安全验证' in body[:2000]:
        return 'ck 通过（token 有效）；落到「安全验证」页（反自动化层）'
    return 'HTTP %s%s' % (status, (' code=' + code) if code else '')


def download(url, path):
    st, body, _ = fetch(url)
    if st != 200:
        raise RuntimeError('下载 build 失败 HTTP %s: %s' % (st, url))
    io.open(path, 'w', encoding='utf-8', newline='').write(body)
    return len(body)


def run_live_build(meta, build_hash):
    """关键：必须用**当次会话的 build**跑，不能用固定 build。

    端到端 302 的配方是
    「拉当次 build JS → 用本环境跑 → 位图换成真机不变常量」。
    本环境现在**天然**产出那个位图，所以只要 build 对，就应通过。
    """
    bp = P('.probe', 'oracle', 'builds', '%s.js' % build_hash[:16])
    os.makedirs(os.path.dirname(bp), exist_ok=True)
    url = 'https://static.zhihu.com/zse-ck/v4/%s.js' % build_hash
    n = download(url, bp)
    print('   已下载 build: %s (%d B)' % (build_hash[:16], n))
    # run_build_live.js 从 .probe/oracle/meta2.txt 读 meta
    io.open(P('.probe', 'oracle', 'meta2.txt'), 'w', encoding='utf-8', newline='').write(meta)
    env = dict(os.environ)
    env.update({'BUILD': bp, 'BUILD_URL': url, 'GTRACE': '0', 'WAIT_MS': '9000'})
    r = subprocess.run(['node', P('.probe', 'zsec', 'run_build_live.js')], cwd=ROOT,
                       env=env, capture_output=True, text=True, timeout=600)
    out = (r.stdout or '') + (r.stderr or '')
    txt = P('.probe', 'zsec', 'ck_build.txt')
    ck = io.open(txt, encoding='utf-8').read().strip() if os.path.exists(txt) else ''
    if not CK_RE.fullmatch(ck or ''):
        m = CK_RE.search(out)
        ck = m.group(0) if m else ''
    return ck, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default=DEFAULT_URL)
    ap.add_argument('--no-submit', action='store_true', help='只算 ck，不提交')
    ap.add_argument('--sleep', type=float, default=8.0, help='两次请求之间的等待秒数')
    ap.add_argument('--meta', default=None, help='直接用这个 meta（不再自己抓挑战）—— 用于「替换真机 ck」对照')
    ap.add_argument('--build', default=None, help='配合 --meta：指定 build 哈希')
    ap.add_argument('--out', default=None, help='把 ck（不含 meta）写到该文件')
    a = ap.parse_args()

    if a.meta:
        print('== 1) 使用指定的 meta（跳过抓挑战）==')
        meta = a.meta
        if not re.fullmatch(r'[A-Za-z0-9+/=]+', meta):
            print('   !! --meta 不是纯 base64'); return 2
        print('   meta  = %d 字符  %s…' % (len(meta), meta[:40]))
        if not a.build:
            print('   !! --meta 必须配 --build'); return 2
        ck, out = run_live_build(meta, a.build)
        if not ck:
            print('   !! 未能产出 ck')
            print('\n'.join(out.strip().splitlines()[-14:]))
            return 3
        print('   __g.ck = %d 字符  %s…' % (len(ck), ck[:40]))
        if a.out:
            io.open(a.out, 'w', encoding='utf-8', newline='').write(ck)
            print('   已写 %s' % a.out)
        else:
            io.open(os.path.join(PB, 'ck_only.txt'), 'w', encoding='utf-8', newline='').write(ck)
        return 0

    print('== 1) 抓全新挑战 ==')
    st, body, hdrs = fetch(a.url)
    print('   HTTP %s | %d B | %s' % (st, len(body), classify(st, body)))
    m = META_RE.search(body)
    if not m:
        print('   !! 页面里没有 <meta id="zh-zse-ck"> —— 可能被风控或页面结构变了')
        io.open(P('.probe', 'zsec', '_fresh_body.html'), 'w', encoding='utf-8').write(body)
        print('   原始 body 已存 .probe/zsec/_fresh_body.html')
        return 2
    meta = m.group(1)
    bm = BUILD_RE.search(body)
    print('   meta  = %d 字符  %s…' % (len(meta), meta[:40]))
    print('   build = %s' % (bm.group(1).rsplit('/', 1)[-1] if bm else '(页面未给)'))
    if not re.fullmatch(r'[A-Za-z0-9+/=]+', meta):
        print('   !! meta 不是纯 base64，异常')
        return 2

    print('\n== 2) 用【当次会话的 build】跑挑战，产出 __g.ck ==')
    if not bm:
        print('   !! 页面没给 build 脚本 URL，无法走活体路径')
        return 2
    build_hash = bm.group(1).rsplit('/', 1)[-1]
    if build_hash.endswith('.js'):
        build_hash = build_hash[:-3]
    ck, out = run_live_build(meta, build_hash)
    if not ck:
        print('   !! 未能产出 ck；输出尾部：')
        print('\n'.join(out.strip().splitlines()[-14:]))
        return 3
    print('   __g.ck = %d 字符  %s…' % (len(ck), ck[:40]))
    print('   panic  = %d' % out.count('panic'))
    for ln in out.splitlines():
        if ('位图' in ln) or ('错误数' in ln):
            print('   ' + ln.strip())

    cookie = '__zse_ck=%s-%s' % (ck, meta)
    print('\n== 3) 拼 cookie ==')
    print('   %s' % cookie[:88] + '…')
    print('   长度 = %d（ck %d + "-" 1 + meta %d）' % (len(cookie) - len('__zse_ck='), len(ck), len(meta)))
    io.open(P('.probe', 'zsec', 'fresh_cookie.txt'), 'w', encoding='utf-8').write(cookie)
    print('   已存 .probe/zsec/fresh_cookie.txt')

    if a.no_submit:
        print('\n== 4) 跳过提交（--no-submit）==')
        return 0

    print('\n== 4) 带 cookie 提交（等 %.0fs 避免风控）==' % a.sleep)
    time.sleep(a.sleep)
    st2, body2, h2 = fetch(a.url, cookie=cookie, follow=False)
    loc = h2.get('Location', '')
    kind = classify(st2, body2, loc)
    print('   HTTP %s | %d B | Location=%s' % (st2, len(body2), (loc[:70] or '(无)')))
    print('   => %s' % kind)
    io.open(P('.probe', 'zsec', '_fresh_submit.html'), 'w', encoding='utf-8').write(body2)
    # 判定：只要**不再是挑战页**，就说明 token 检查通过
    ok = (st2 in (301, 302, 303, 307, 308)) or ('<meta id="zh-zse-ck"' not in body2)
    print('\n%s' % ('成功：服务端接受了这个全新 ck' if ok else '✗ 未通过：' + kind))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
