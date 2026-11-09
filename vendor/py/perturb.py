# -*- coding: utf-8 -*-
"""扰动实验：改一个环境因素跑一次，看位图哪个字/位变了，从而反查守卫出处。"""
import subprocess, io, re, os, sys, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, '.probe/oracle/live_clean.js')
BURL = 'https://static.zhihu.com/zse-ck/v4/6d0ccece4cfcad97.js'


def run(extra, tag):
    env = dict(os.environ, BUILD=BUILD, BUILD_URL=BURL, WAIT_MS='9000')
    env.update(extra)
    r = subprocess.run(['node', os.path.join(ROOT, '.probe/zsec/run_build_live.js')],
                       capture_output=True, text=True, env=env, timeout=240, cwd=ROOT)
    err = re.search(r'错误数: (\d+)', r.stdout)
    mem = open(os.path.join(ROOT, '.probe/zsec/mem_build.bin'), 'rb').read()
    m = re.search(rb'[\x20-\x7e]{48}[0-9a-f]{32}z[0-9a-f]{12}', mem)
    if not m:
        return tag, None, None, '无 record93'
    rec = m.group().decode()
    return tag, rec[:48], rec[48:80], err.group(1) if err else '?'


def fmt(bm):
    return ' '.join(bm[i:i + 4] for i in range(0, 48, 4)) if bm else '(无)'


def main():
    cases = [
        ({}, '基线'),
        ({'ZSE_NI_NOFILTER': '1'}, '去掉 createNodeIterator().filter'),
        ({'ZSE_WDOC': '1'}, 'document 可写（SRC0 返回 2）'),
        ({'ZSE_ALLUNDEF': '1'}, 'document.all 返回 undefined'),
    ]
    out = []
    for extra, tag in cases:
        t, bm, fp, err = run(extra, tag)
        out.append((tag, bm, fp, err))
        print('%-34s 错误数=%-3s 位图=%s' % (tag, err, fmt(bm)), flush=True)
    print()
    base = out[0][1]
    if base:
        bw = [base[i:i + 4] for i in range(0, 48, 4)]
        for tag, bm, fp, err in out[1:]:
            if not bm:
                continue
            w = [bm[i:i + 4] for i in range(0, 48, 4)]
            d = []
            for i in range(12):
                if w[i] != bw[i]:
                    a, b = int(w[i], 16), int(bw[i], 16)
                    d.append('w%d: %s->%s 置位=%s 清位=%s' % (i, bw[i], w[i], hex(a ^ b & ~a), hex(b ^ a & ~b)))
            print('%-34s 差异: %s' % (tag, '; '.join(d) if d else '无'))


if __name__ == '__main__':
    main()
