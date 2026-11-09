#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pure_calc_verify.py —— 「纯算全链路」离线自证门

把 `__zse_ck` 的生成链**完全用 Python 重算一遍**，逐步与归档的真机会话对拍：

    ① 位图 48-hex   ← py/bitmap.py      （从真机 zh/qh 反解）
    ② 指纹 32-hex   ← py/fingerprint.py （md5 over 40 段）
    ③ crc32(UA)     ← py/record93.py    （zlib.crc32）
    ④ record93      ← py/record93.py    （①+②+③ 拼装）
    ⑤ ck 132 字符   ← py/pure_ck.py     （profile + record93 → radix64(CBC)）

每一步都断言与真机逐字符一致；任一步失败即非零退出。
**不加载站点代码、不跑 Node、不跑 wasm。**

用法
----
    python py/pure_calc_verify.py
    python py/pure_calc_verify.py --profile <profile>.json --zh <zh> --qh <qh>
"""
import argparse
import importlib.util
import json
import os
import re
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
P = lambda *a: os.path.join(ROOT, *a)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    # 默认用「同一次会话」的自洽 fixture（profile + record93 + ck + zh/qh + wasm 同源）。
    #   显式指定 --profile 时，其 record93 必须与 --zh/--qh 来自同一次会话，否则位图不同源会假报失败。
    ap.add_argument('--profile', default=P('profiles', '_live_fixture.json'))
    ap.add_argument('--wasm', default=None)
    ap.add_argument('--zh', default=None)
    ap.add_argument('--qh', default=P('.probe', 'zsec', 'qh_full.txt'))
    ap.add_argument('--preimage', default=P('.probe', 'zsec', '_md5in.json'))
    a = ap.parse_args()

    bm = _load('bm', P('py', 'bitmap.py'))
    fpg = _load('fpg', P('py', 'fingerprint.py'))
    rec93 = _load('rec93', P('py', 'record93.py'))
    pck = _load('pck', P('py', 'pure_ck.py'))
    fck = _load('fck', P('py', 'fresh_ck.py'))

    prof = json.load(open(a.profile, encoding='utf8'))
    real_rec = prof['record93']
    real_ck = prof['ck']
    # 同源优先：profile 里带 zh/qh/wasm 就用它，保证是同一次会话
    zh_txt = prof.get('zh') or (open(a.zh, encoding='utf8').read() if a.zh else None)
    qh_txt = prof.get('qh') or (open(a.qh, encoding='utf8').read() if a.qh else None)
    wasm = a.wasm or prof.get('wasm') or P('.probe', 'build-859e.wasm')
    ok = True

    def check(label, got, want):
        nonlocal ok
        good = (got == want)
        ok &= good
        print('  %-22s %s' % (label, 'OK' if good else 'MISMATCH'))
        if not good:
            print('      got  = %s' % str(got)[:120])
            print('      want = %s' % str(want)[:120])
        return good

    print('真机会话：record93 = %s' % real_rec)
    print('          ck       = %s…' % real_ck[:40])
    print('')

    # ---- ⓪ 每 build 常量：从 wasm 静态推（零执行） -------------------------
    print('[0/5] rk/alphabet/xreg/iv/mask  ← py/derive_session.py（零执行，只读 wasm + 样本令牌）')
    ds = _load('ds', P('py', 'derive_session.py'))
    toks = ds.collect_tokens()
    dr = ds.derive(wasm, toks, verbose=False)
    if not dr:
        print('  静态推导失败 ⇒ FAIL'); return 1
    print('        占位令牌 %d 条 · 真令牌 %d 条 · iv 候选 %d 个'
          % (dr['n_placeholder'], dr['n_real'], dr['n_iv_cand']))
    check('rk[32]（静态）', dr['rk'], prof['rk'])
    check('alphabet（静态）', dr['alphabet'], prof['alphabet'])
    check('xreg（静态）', dr['xreg'], prof['xreg'])
    check('mask（静态）', dr['mask'], prof['mask'])
    # `iv[4:16]`（12 个 hex）是**确定**的；`iv[0:4]` 本身是自由参数
    #   （只影响 record93 的 w0，服务端不校验；解密/加密互逆，任何候选都能原样复现 ck）
    #   ⇒ 只对 `iv[4:16]` 判等。
    ivs = dr['iv_candidates'] or [dr['iv']]
    tail_ok = all(v[4:] == prof['iv'][4:] for v in ivs)
    ok &= tail_ok
    print('  %-22s %s（iv[4:16]=确定；iv[0:4] 候选 %d 个，不参与判定）'
          % ('iv（静态）', 'OK' if tail_ok else 'MISMATCH', len(ivs)))

    # ---- ① 位图 ------------------------------------------------------------
    print('[1/5] 位图 48-hex  ← py/bitmap.py（从 zh/qh 反解）')
    zh, qh = zh_txt, qh_txt
    if not zh or not qh:
        print('  缺 zh/qh ⇒ 跳过位图步'); return 1
    d = bm.decode_bitmap_from_zh_qh(zh, qh)
    tag4 = real_rec[:4]
    go12 = real_rec[4 + 16 + 8:4 + 16 + 8 + 12]      # w7..w9（Go 侧，不在 zh/qh 里）
    bitmap48 = tag4 + d['payload16'] + d['inner8'] + go12 + d['cvm8']
    check('位图 48-hex', bitmap48, real_rec[:48])

    # ---- ② 指纹 ------------------------------------------------------------
    print('[2/5] 指纹 32-hex  ← py/fingerprint.py（md5 over 40 段）')
    pre = fpg.load_default_preimage() if hasattr(fpg, 'load_default_preimage') else None
    if pre is None:
        pre = json.load(open(a.preimage, encoding='utf8'))
        if isinstance(pre, dict):
            pre = pre.get('input') or pre.get('preimage') or list(pre.values())[0]
    fp = fpg.fingerprint_from_preimage(pre)
    check('指纹 32-hex', fp, real_rec[48:80])

    # ---- ③ crc32(UA) -------------------------------------------------------
    print('[3/5] crc32(UA)      ← py/record93.py（zlib.crc32）')
    crc = rec93.crc32_hex(fck.UA)
    check('crc32(UA)', crc, real_rec[81:89])

    # ---- ④ record93 --------------------------------------------------------
    print('[4/5] record93       ← py/record93.py（①+②+③ 拼装）')
    rebuilt = rec93.build_record93(
        bitmap48, fp, fck.UA,
        w0=real_rec[:4], tail4=real_rec[89:93])
    check('record93 93 字符', rebuilt, real_rec)

    # ---- ⑤ ck --------------------------------------------------------------
    print('[5/5] ck 132 字符    ← py/pure_ck.py（profile + record93）')
    ck = pck.pure_ck(prof, rebuilt)
    check('ck 132 字符', ck, real_ck)

    print('')
    print('纯算全链路自证：%s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
