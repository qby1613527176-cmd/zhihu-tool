#!/usr/bin/env python3
"""record93 纯组装 —— 93 字符 = 位图48 + 指纹32 + 'z' + crc32(UA)8 + tail4_4

    record93 = <48 hex 位图> <32 hex md5 指纹> 'z' <8 hex crc32(UA)> <4 hex tail>

其中：
  · 位图 12 字（w0..w11），w0 与 tail4 是**每代随机 4-hex**（本环境实测 20/20 唯一）
  · 指纹 = md5(40 个字段 join('|'))，见 js/fingerprint.restored.js
  · crc32 = zlib.crc32(请求实际用的 UA)，8 位小写 hex

用法
----
  from record93 import build_record93, BITMAP_REAL
  rec = build_record93(BITMAP_REAL, fingerprint_hex, ua)

  python py/record93.py --bitmap 2000 1000 8000 4030 8000 8000 8800 8000 8000 8000 4000 \
                        --fp be544511d8accf5879de9179e434e829 --ua "<UA>" [--w0 xxxx] [--tail xxxx]
"""
import zlib, random, sys, argparse

# 稳定位图（w1..w11）
#
# 原值 `2000 1000 8000 4030 8000 8000 8800 8000 8000 8000 4000` 会让会话掉进
# `/account/unhuman`；逐字段扫描（同 build、同 meta、只改位图）得到：
#
#   w4=0x4000 → 302 /people/zhihuadmin（真实页）   ← 通过
#   w4=0x4030 → 302 /account/unhuman               ← 被标记
#   w7=0x8000 或 0x8800 → 都通过（该字段不影响判定）
#
# ⇒ 决定性字段只有 **w4**：必须是 `0x4000`，不能带 bit4/bit5。
#   对应 payload 层 v6 的两条守卫（`Object.keys(window)` 里出现 `__g`、以及一条 URL 检查）——
#   我们的 handenv 把这两条误触发了，真机不会。修 handenv 是根因解，改这里是立即解。
BITMAP_REAL = '2000 1000 8000 4000 8000 8000 8000 8000 8000 8000 4000'
# 旧值（会被反自动化层标记，保留供对照）
BITMAP_FIREFOX_ERA = '2000 1000 8000 4030 8000 8000 8800 8000 8000 8000 4000'


def crc32_hex(ua: str) -> str:
    return '%08x' % (zlib.crc32(ua.encode('utf-8')) & 0xffffffff)


def build_record93(bitmap: str, fingerprint: str, ua: str,
                   w0: str = None, tail4: str = None) -> str:
    """bitmap: 12 个字（空格分隔）或 48 hex；fingerprint: 32 hex；ua: 字符串。"""
    words = bitmap.split()
    if len(words) == 11:          # 只给了 w1..w11（稳定字），补随机的 w0
        if w0 is None:
            w0 = '%04x' % random.randrange(0x10000)
        words = [w0] + words
        bm = ''.join(words)
    elif len(words) == 12:        # 已含 w0
        if w0 is not None:
            words[0] = w0
        bm = ''.join(words)
    else:
        bm = bitmap.replace(' ', '')
    assert len(bm) == 48, '位图必须 48 hex'
    fp = fingerprint.replace(' ', '')
    assert len(fp) == 32, '指纹必须 32 hex'
    if tail4 is None:
        tail4 = '%04x' % random.randrange(0x10000)
    rec = bm + fp + 'z' + crc32_hex(ua) + tail4
    assert len(rec) == 93
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bitmap', default=BITMAP_REAL, help='12 个字（空格分隔）或 48 hex')
    ap.add_argument('--fp', required=True, help='32 hex 指纹')
    ap.add_argument('--ua', required=True)
    ap.add_argument('--w0', default=None)
    ap.add_argument('--tail', default=None)
    a = ap.parse_args()
    print(build_record93(a.bitmap, a.fp, a.ua, a.w0, a.tail))
    return 0


if __name__ == '__main__':
    sys.exit(main())
