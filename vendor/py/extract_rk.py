#!/usr/bin/env python3
"""从 wasm 线性内存 dump 里提取 32 个 SM4 round key（无需浏览器 trace）

结论（2026-10-08 实测闭环）
--------------------------
  rk[i] = traced[i] ^ 0x7B7B7B7B

其中 traced 是 Go 侧交给 JS VM 的 32 个十进制字符串（每个在内存里出现两次，
地址相差 0x10）。它们**不静态存在于 .wasm 文件里**（32/32 搜不到），
是运行期由 build 内嵌数据算出来的 ⇒ **rk 是 build 常量**：
  · 4 个刻意不同的环境（env1/env2/env3/env_win）→ rk 完全相同（0/32 位差异）
  · 3 个不同 build（859e / 5e26 / 0eaa）    → rk 三组全不同

验证（两条独立路径互相印证）
  路径 A：从会话 trace 反解（S 盒输出 + 状态链，见 .probe/vmsvc/extract_params.py）
  路径 B：本脚本 —— 内存直读 + XOR 0x7B7B7B7B
  两者对 build-859e 给出**逐位相同的 32 个值**。

用法
----
  python py/extract_rk.py <mem.bin> [--json out.json]

mem.bin = 驱动跑完后落地的 wasm 线性内存快照（如 .probe/zsec/mem_mine.bin）
"""
import re, sys, json, argparse


def scan_pairs(buf, min_digits=5, max_digits=10):
    """找所有「同一十进制值出现在 X 与 X+0x10」的位置，按地址排序去重。

    5 位下限用来滤掉 0/1/2 这类到处成对出现的噪声。
    """
    pat = re.compile(rb'(?<![0-9])([0-9]{%d,%d})(?![0-9])' % (min_digits, max_digits))
    raw = [(m.start(), int(m.group(1))) for m in pat.finditer(buf)]
    pos = {}
    for a, v in raw:
        pos.setdefault(v, []).append(a)
    out, seen = [], set()
    for a, v in raw:
        if a in seen:
            continue
        if a + 0x10 in pos.get(v, []):
            out.append((a, v))
            seen.add(a); seen.add(a + 0x10)
    return out


def find_key_cluster(buf, want=32):
    """在内存里找密钥簇：连续「成对十进制串」，且**互不相同**的个数 >= want-2。

    只用簇长度会被大片 0 值噪声骗到（实测），所以按 distinct 计数打分。
    """
    pairs = scan_pairs(buf)
    if not pairs:
        return None
    pairs.sort()
    best = None
    i = 0
    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and pairs[j][0] - pairs[j - 1][0] <= 0x200:
            j += 1
        seg = pairs[i:j]
        if len(seg) >= want:
            distinct = len(set(v for _, v in seg))
            if distinct >= want - 2 and (best is None or distinct > best[0]):
                best = (distinct, seg)
        i = j
    if best is None:
        return None
    return best[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mem')
    ap.add_argument('--json', default=None)
    ap.add_argument('--xormask', default='0x7B7B7B7B')
    a = ap.parse_args()

    buf = open(a.mem, 'rb').read()
    clus = find_key_cluster(buf)
    if not clus:
        print('未找到密钥簇'); return 2
    traced = [v for _, v in clus]
    if len(traced) > 32:
        traced = traced[:32]
    if len(traced) < 32:
        print('簇只有 %d 个值（<32）' % len(traced)); return 3
    xm = int(a.xormask, 16)
    rk = [v ^ xm for v in traced]
    print('traced 首地址 0x%x  个数 %d' % (clus[0][0], len(traced)))
    print('rk = traced ^ %#x' % xm)
    for i, v in enumerate(rk):
        print('  rk[%2d] = %#010x' % (i, v))
    if a.json:
        json.dump({'traced': traced, 'rk': rk, 'xor': xm, 'addr0': clus[0][0]},
                  open(a.json, 'w'), indent=1)
        print('已写', a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
