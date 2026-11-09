#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""derive_and_submit.py —— 盲测：只给一个 build，**零执行**推出会话常量并提交验证

流程（全部纯 Python，不跑 Node / 不跑 wasm）：
  ① GET 目标页 → 挑战页 → 解析 meta + build URL；下载 build JS
  ② 从 build JS 里抽出内嵌 wasm
  ③ `py/derive_session.py` 零执行推出 rk/alphabet/xreg/mask/iv（iv 可能留若干候选）
  ④ 对每个 iv 候选：profile → record93 → ck → 提交 → 按 classify 判读
  ⑤ 报告哪个候选被服务端接受（302 → 真实页 即成功）

用法
----
    python py/derive_and_submit.py                 # 全自动
    python py/derive_and_submit.py --max 20 --sleep 1.5
"""
import argparse
import base64
import importlib.util
import io
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
P = lambda *a: os.path.join(ROOT, *a)
sys.path.insert(0, HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default=None)
    ap.add_argument('--ua', default=None)
    ap.add_argument('--max', type=int, default=80, help='最多试多少个 iv 候选')
    ap.add_argument('--sleep', type=float, default=1.2, help='每次提交之间的间隔（秒）')
    ap.add_argument('--dry', action='store_true', help='只算不提交（打印第一个候选的 ck 与 cookie）')
    a = ap.parse_args()

    fresh_ck = _load('fresh_ck', P('py', 'fresh_ck.py'))
    record93 = _load('record93', P('py', 'record93.py'))
    fpg = _load('fpg', P('py', 'fingerprint.py'))
    pck = _load('pck', P('py', 'pure_ck.py'))
    ds = _load('ds', P('py', 'derive_session.py'))
    url = a.url or fresh_ck.DEFAULT_URL
    ua = a.ua or fresh_ck.UA

    # ---- ① 挑战页 + build JS ------------------------------------------------
    print('① GET %s' % url)
    st, body, _ = fresh_ck.fetch(url)
    print('   HTTP %s | %d B | %s' % (st, len(body), fresh_ck.classify(st, body)))
    m = fresh_ck.META_RE.search(body or '')
    bm = fresh_ck.BUILD_RE.search(body or '')
    if not m or not bm:
        print('   !! 拿不到 meta / build URL')
        return 2
    meta = m.group(1)
    js_url = bm.group(1)
    build = js_url.rsplit('/', 1)[-1][:-3]
    print('   meta = %d 字符   build = %s' % (len(meta), build))

    st2, js, _ = fresh_ck.fetch(js_url)
    if st2 != 200:
        print('   !! 下载 build 失败 HTTP %s' % st2)
        return 2
    print('   build JS = %d B' % len(js))

    # ---- ② 抽 wasm ---------------------------------------------------------
    i = js.find('AGFzbQ')
    if i < 0:
        print('   !! build JS 里找不到内嵌 wasm')
        return 2
    j = i
    while j < len(js) and (js[j].isalnum() or js[j] in '+/='):
        j += 1
    wasm = base64.b64decode(js[i:j] + '=' * ((4 - (j - i) % 4) % 4))
    wp = P('.probe', 'tmp', 'blind_%s.wasm' % build[:8])
    open(wp, 'wb').write(wasm)
    print('   wasm = %d B  -> %s' % (len(wasm), wp))

    # ---- ③ 零执行推会话常量 ------------------------------------------------
    print('\n③ derive_session（零执行）')
    toks = ds.collect_tokens()
    dr = ds.derive(wp, toks, verbose=False)
    if not dr:
        print('   !! 推不出来')
        return 2
    print('   占位 %d · 真令牌 %d · iv 候选 %d' % (dr['n_placeholder'], dr['n_real'], dr['n_iv_cand']))
    print('   xreg=%#04x  mask=%#04x  rk[0]=%#010x' % (dr['xreg'], dr['mask'], dr['rk'][0]))
    print('   alphabet = %s' % dr['alphabet'])
    cands = dr['iv_candidates'] or [dr['iv']]
    print('   iv 候选：%d 个%s' % (len(cands), '' if len(cands) == 1 else '（逐个试）'))

    # ---- ④ 指纹 / record93 素材 -------------------------------------------
    fp = fpg.fingerprint_from_preimage(fpg.load_default_preimage())
    print('\n④ 环境指纹 md5 = %s' % fp)

    # ---- ⑤ 逐候选提交 -----------------------------------------------------
    print('\n⑤ 逐候选提交（meta 固定，只换 payload）')
    hit = None
    tried = 0
    for iv in cands:
        if tried >= a.max:
            print('   （已达 --max %d，停止）' % a.max)
            break
        tried += 1
        prof = {'rk': dr['rk'], 'alphabet': dr['alphabet'], 'xreg': dr['xreg'],
                'mask': dr['mask'], 'iv': iv}
        rec = record93.build_record93(record93.BITMAP_REAL, fp, ua,
                                      w0='%04x' % random.randrange(0x10000),
                                      tail4='%04x' % random.randrange(0x10000))
        ck = pck.pure_ck(prof, rec)
        cookie = '%s-%s' % (ck, meta)
        if a.dry:
            print('\n   [dry] 不提交。iv = %s' % iv)
            print('   ck     = %s' % ck)
            print('   cookie = __zse_ck=%s' % cookie)
            print('   record93 = %s' % rec)
            return 0
        st3, body3, hdr3 = fresh_ck.fetch(url, cookie='__zse_ck=%s' % cookie, follow=False)
        loc = hdr3.get('Location', '') if hasattr(hdr3, 'get') else ''
        cls = fresh_ck.classify(st3, body3, loc)
        flag = ''
        if 'unhuman' in cls:
            flag = '  <<< token 被接受，但会话被标记（非真实页）'
        elif '通过' in cls:
            flag = '  <<< 完全通过（真实页）'
        print('   [%2d/%2d] iv=%s  HTTP %s  %s%s' % (tried, len(cands), iv, st3, cls, flag))
        if '40362' in cls:
            print('   （风控限流，等 20s 再继续）')
            time.sleep(20)
            continue
        if ('通过' in cls) and ('unhuman' not in cls):
            hit = iv
            break
        if 'unhuman' in cls:
            hit = iv          # token 已被接受；落点是会话级标记，不再换候选
            break
        time.sleep(a.sleep)
    print('')
    if hit:
        print('盲测结果：iv = %s 的 token **被服务端接受**（302）' % hit)
        print('  判读以 Location 为准：/people/zhihuadmin = 完全通过（真实页）；'
              '/account/unhuman = token 有效但会话被反自动化层标记。')
        return 0
    print('盲测结果：%d 个候选都没拿到「完全通过」（见上面分类；unhuman 也算 token 通过）' % tried)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
