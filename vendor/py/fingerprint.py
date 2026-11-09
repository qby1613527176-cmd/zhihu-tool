#!/usr/bin/env python3
"""指纹（32-hex md5）—— 纯 Python，不依赖浏览器 / Node

算法（实测闭环，见 js/fingerprint.restored.js）：
    指纹 = md5_hex( utf8( 40 个字段用 '|' 连接 ) )

40 个字段的顺序（实测）：
    [0]      navigator.hardwareConcurrency（取不到 = -1）
    [1]      WebGL UNMASKED_VENDOR_WEBGL
    [2]      WebGL UNMASKED_RENDERER_WEBGL
    [3]      canvas.toDataURL()（本环境 822 字符）
    [4..34]  探测命中的字体名（按探测列表顺序，本环境 31 条）
    [35..39] navigator.plugins 的 name（本环境 5 条）

自检真值：
    入参 len = 1388（UTF-8 字节）
    md5    = be544511d8accf5879de9179e434e829
    == 真机 record93 的 32-hex 指纹字段，逐字命中。

用法
----
    from fingerprint import fingerprint_from_preimage, load_default_preimage
    fp = fingerprint_from_preimage(load_default_preimage())     # -> 'be544511…'

    python py/fingerprint.py --selftest
    python py/fingerprint.py --fields '8|Google Inc. …'
    python py/fingerprint.py --env env.json          # {"hardwareConcurrency":8, "webglVendor":…, …}
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
P = lambda *a: os.path.join(ROOT, *a)

# 真机捕获的入参（40 段 '|' 连接）—— 自检基准
REAL_PREIMAGE_SHA1 = None
REAL_FP = 'be544511d8accf5879de9179e434e829'

# 字体探测列表（顺序即字段顺序；命中才计入）
FONT_LIST = [
    'Arial', 'Arial Black', 'Arial Narrow', 'Calibri', 'Cambria Math', 'Comic Sans MS',
    'Consolas', 'Courier', 'Courier New', 'Georgia', 'Helvetica', 'Lucida Console',
    'Lucida Sans Unicode', 'Microsoft Sans Serif', 'MS PGothic', 'MS Sans Serif', 'MS Serif',
    'Palatino Linotype', 'Segoe Print', 'Segoe Script', 'Segoe UI', 'Segoe UI Light',
    'Segoe UI Semibold', 'Tahoma', 'Times', 'Times New Roman', 'Trebuchet MS', 'Verdana',
    'Wingdings', 'Wingdings 2', 'Wingdings 3',
]


def md5_hex(s: str) -> str:
    """md5 over UTF-8 字节（等价于原混淆里的 unescape(encodeURIComponent(s)) → str2binl）。"""
    return hashlib.md5(s.encode('utf-8')).hexdigest()


def fingerprint_from_preimage(preimage: str) -> str:
    return md5_hex(preimage)


def fingerprint_from_fields(fields) -> str:
    return md5_hex('|'.join(str(f) for f in fields))


def load_default_preimage():
    """读真机捕获的入参（.probe/zsec/_md5in.json，元素为 preimage 字符串）。"""
    for c in (P('.probe', 'zsec', '_md5in.json'),
              P('evidence', '_md5in.json'),
              os.path.join(HERE, '_md5in.json')):
        if os.path.exists(c):
            d = json.load(open(c, encoding='utf-8'))
            if isinstance(d, list) and d:
                return d[0]
            if isinstance(d, str):
                return d
    return None


def collect(env: dict) -> list:
    """按 40 字段顺序组装。env 缺哪项就按真机的“取不到”语义填（-1 / 空串 / 跳过）。"""
    f = []
    f.append(env.get('hardwareConcurrency') or -1)
    f.append(env.get('webglVendor', ''))
    f.append(env.get('webglRenderer', ''))
    f.append(env.get('canvasDataURL', ''))
    hit = env.get('fonts')
    if hit is None:
        hit = [x for x in FONT_LIST if x in set(env.get('installedFonts', []))]
    f.extend(hit)
    f.extend(env.get('plugins', []))
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--fields', default=None, help="已拼好的 40 段入参（'|' 连接）")
    ap.add_argument('--env', default=None, help='env.json，含 hardwareConcurrency/webglVendor/…')
    a = ap.parse_args()

    if a.fields:
        print(fingerprint_from_preimage(a.fields)); return 0
    if a.env:
        env = json.load(open(a.env, encoding='utf-8'))
        pre = '|'.join(str(x) for x in collect(env))
        print('preimage len = %d' % len(pre.encode('utf-8')))
        print(fingerprint_from_preimage(pre)); return 0
    if a.selftest or True:
        pre = load_default_preimage()
        if pre is None:
            print('找不到真机入参 _md5in.json（用 --fields 直接给）'); return 2
        got = fingerprint_from_preimage(pre)
        print('preimage len = %d 段数 = %d' % (len(pre.encode('utf-8')), len(pre.split('|'))))
        print('md5 = %s' % got)
        ok = (got == REAL_FP)
        print('自检 vs 真机 %s: %s' % (REAL_FP, '一致' if ok else '不一致!'))
        return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
