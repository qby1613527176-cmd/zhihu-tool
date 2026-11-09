# -*- coding: utf-8 -*-
"""跨 build 比对运行期生成物：11841 服务程序 与 136041 生成代码。
若两者逐字节相同 ⇒ 换 hash 只是换壳（算法与生成物不变）。"""
import subprocess, io, os, shutil, hashlib, glob, re, sys

BUILDS = [
    ('A_old', '.probe/oracle/live_08436d23.js'),
    ('B_new', '.probe/oracle/live_fresh.js'),
]
res = {}
for tag, bp in BUILDS:
    if not os.path.exists(bp):
        print('缺 build', bp); continue
    env = dict(os.environ, BUILD=bp, GTRACE='0', WAIT_MS='9000')
    env.pop('NOEVAL', None)          # 需要 eval 包装器才能截获生成物
    r = subprocess.run(['node', '.probe/zsec/run_build_live.js'], capture_output=True,
                       text=True, env=env, timeout=200)
    err = re.search(r'错误数: (\d+)', r.stdout)
    got = {}
    for src, dst in [('.probe/oracle/cur_eval_11841.js', '.probe/zsec/gen_%s_11841.js' % tag),
                     ('.probe/zsec/eval_arg_big.js', '.probe/zsec/gen_%s_136041.js' % tag)]:
        if os.path.exists(src):
            shutil.copy(src, dst)
            d = io.open(dst, 'rb').read()
            got[os.path.basename(dst)] = (len(d), hashlib.sha256(d).hexdigest())
    res[tag] = got
    print('%-6s 错误数=%s  %s' % (tag, err.group(1) if err else '?',
          {k: (v[0], v[1][:16]) for k, v in got.items()}))

print('\n=== 跨 build 比对 ===')
for kind in ('11841', '136041'):
    a = res.get('A_old', {}).get('gen_A_old_%s.js' % kind)
    b = res.get('B_new', {}).get('gen_B_new_%s.js' % kind)
    if a and b:
        print('%-7s A=%d/%s  B=%d/%s  %s' % (kind, a[0], a[1][:16], b[0], b[1][:16],
              'IDENTICAL' if a[1] == b[1] else 'DIFFER'))
    else:
        print('%-7s 缺样本 A=%s B=%s' % (kind, bool(a), bool(b)))
