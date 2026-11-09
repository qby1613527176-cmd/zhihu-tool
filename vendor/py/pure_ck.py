#!/usr/bin/env python3
"""纯算生成 __zse_ck —— 不跑 wasm、不跑挑战 JS、不依赖任何 trace

    ck = '005_' + radix64_encode(
             encrypt_96( record93 + PKCS#7(3), rk, iv, mask ),
             alphabet, xreg )

输入
----
  profile.json  ← `python py/build_profile.py <mem.bin> -o profile.json` 的产物
                  （只含与 build 有关的量：rk / alphabet / xreg / iv / mask）
  record93      93 字符，或省略则用 profile 里那份

输出
----
  完整 cookie 的两段：
    payload = '005_' + 128 字符        （客户端算出来的那半）
    cookie  = payload + '-' + meta     （meta 由服务端随挑战下发）

用法
----
  python py/pure_ck.py profile.json
  python py/pure_ck.py profile.json <record93> [--meta <128字符meta>]
"""
import sys, os, json, argparse, importlib.util, random


def load_cipher():
    for c in (os.path.join(os.path.dirname(os.path.abspath(__file__)), 'zse_cipher.py'),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.probe', 'vmsvc', 'zse_cipher.py')):
        c = os.path.normpath(c)
        if os.path.exists(c):
            spec = importlib.util.spec_from_file_location('zc', c)
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            return m
    raise SystemExit('找不到 zse_cipher.py')


def pure_ck(profile, rec93):
    zc = load_cipher()
    rk = profile['rk']
    al = profile['alphabet']
    xr = profile['xreg']
    iv = profile['iv'].encode('latin1') if isinstance(profile['iv'], str) else profile['iv']
    mask = profile['mask']
    rec = rec93.encode('latin1') if isinstance(rec93, str) else rec93
    assert len(rec) == 93, 'record93 必须 93 字节'
    ct = zc.encrypt_96(zc.record93_to_pt(rec), rk, iv=iv, mask=mask)
    return '005_' + zc.radix64_encode(ct, al, xr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('profile')
    ap.add_argument('record93', nargs='?')
    ap.add_argument('--meta', default=None, help='服务端下发的 128 字符 meta，给了就拼完整 cookie')
    ap.add_argument('--ua', default=None, help='给了 --ua 与 --fp 就自己组装 record93')
    ap.add_argument('--fp', default=None, help='32 hex 指纹')
    ap.add_argument('--bitmap', default=None, help='12 个字或 48 hex；默认用实测稳定位图')
    ap.add_argument('--w0', default=None, help='4 hex，不给则随机')
    ap.add_argument('--tail', default=None, help='4 hex，不给则随机')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()

    prof = json.load(open(a.profile, encoding='utf-8'))
    rec = a.record93 or prof.get('record93')
    if a.ua and a.fp:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from record93 import build_record93, BITMAP_REAL
        rec = build_record93(a.bitmap or BITMAP_REAL, a.fp, a.ua, a.w0, a.tail)
        print('record93 = %s' % rec)
    if not rec:
        print('没有 record93'); return 2
    ck = pure_ck(prof, rec)
    print('payload = %s' % ck)
    if a.meta:
        print('cookie  = %s-%s' % (ck, a.meta))
    if a.selftest:
        exp = prof.get('ck')
        print('自检 vs profile.ck: %s' % ('一致' if exp == ck else '不一致!'))
        return 0 if exp == ck else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
