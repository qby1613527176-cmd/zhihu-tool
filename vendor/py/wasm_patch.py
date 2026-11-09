# -*- coding: utf-8 -*-
"""对 build 内嵌的 wasm 打与 §51.1 相同的 3 处补丁（按名字段定位 _00oooO，与 build 无关）：
  在目标函数 body 入口插入 `local.get1; local.get0; 0; 0; call fd_write; drop`（11 字节），
  并正确重编码 body size LEB 与 code 段 size LEB。
用法: python py/wasm_patch.py <build.js> <out.wasm>
"""
import re, io, sys, base64, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wasm_patch_raw import uleb, leb  # noqa: E402


def extract_wasm(js_path):
    s = io.open(js_path, 'r', encoding='utf-8', errors='replace').read()
    for m in re.finditer(r'[A-Za-z0-9+/]{200,}={0,2}', s):
        b64 = m.group()
        try:
            d = base64.b64decode(b64 + '=' * ((4 - len(b64) % 4) % 4))
        except Exception:
            continue
        if d[:4] == b'\x00asm':
            return d, m.start(), len(b64)
    return None, -1, 0


def parse(d):
    secs = []; i = 8
    while i < len(d):
        idp = i; sid = d[i]; i += 1
        sz, i = uleb(d, i)
        secs.append({'id': sid, 'idp': idp, 'size_off': i - len(leb(sz)), 'hdr': i, 'size': sz,
                     'body': d[i:i + sz]})
        i += sz
    return secs


def func_names(secs):
    names = {}
    for s in secs:
        if s['id'] != 0:
            continue
        p = 0; l, p = uleb(s['body'], p)
        if s['body'][p:p + l] != b'name':
            continue
        p += l
        while p < len(s['body']):
            sub = s['body'][p]; p += 1
            ssz, p = uleb(s['body'], p)
            end = p + ssz
            if sub == 1:
                n, q = uleb(s['body'], p)
                for _ in range(n):
                    idx, q = uleb(s['body'], q)
                    ln, q = uleb(s['body'], q)
                    names[idx] = s['body'][q:q + ln].decode('utf-8', 'replace'); q += ln
            p = end
    return names


def main():
    js = sys.argv[1] if len(sys.argv) > 1 else '.probe/oracle/live_clean.js'
    out = sys.argv[2] if len(sys.argv) > 2 else '.probe/vmsvc/wasm_mine_bp.wasm'
    js = js if os.path.isabs(js) else os.path.join(ROOT, js)
    out = out if os.path.isabs(out) else os.path.join(ROOT, out)

    d, b64off, b64len = extract_wasm(js)
    print('wasm %d bytes, base64 @%d len=%d' % (len(d), b64off, b64len))
    secs = parse(d)
    names = func_names(secs)

    nimp = 0
    for s in secs:
        if s['id'] == 2:
            nimp, _ = uleb(s['body'], 0)
    print('导入函数数 = %d' % nimp)

    tgt = [i for i, nm in names.items() if '_00oooO' in nm]
    if not tgt:
        print('名字段没有 _00oooO'); return
    fidx = tgt[0]; cidx = fidx - nimp
    print('_00oooO 函数索引 = %d -> code body#%d' % (fidx, cidx))

    # 定位 code 段与目标 body
    code = [s for s in secs if s['id'] == 10][0]
    p = 0; n, p = uleb(code['body'], p)
    assert n == cidx + 1 or n > cidx, 'code 段函数数 %d 与 body#%d 不符' % (n, cidx)
    body_off_in_sec = p
    for k in range(cidx):
        bsz, p = uleb(code['body'], p)
        p += bsz
    bsz_off = p                       # body size LEB 在段内的偏移
    bsz, p = uleb(code['body'], p)
    bstart = p                        # body 内容在段内的偏移
    print('body#%d: size=%d  size_LEB 段内偏移=%d  内容段内偏移=%d' % (cidx, bsz, bsz_off, bstart))
    print('  原起始 8 字节 = %s' % code['body'][bstart:bstart + 8].hex())

    PRO = bytes([0x20, 0x01, 0x20, 0x00, 0x41, 0x00, 0x41, 0x00, 0x10, 0x00, 0x1A])
    # fd_write 的导入索引 = 0（wasi_snapshot_preview1 是第一个 import 模块）
    new_body = (code['body'][:bsz_off] + leb(bsz + len(PRO)) + PRO
                + code['body'][bsz_off + len(leb(bsz)):])
    print('  body 大小 %d -> %d（+%d）' % (bsz, bsz + len(PRO), len(PRO)))

    new_sec = leb(len(new_body)) + new_body
    new_code = bytes([10]) + new_sec
    # 重组文件：段头（id+旧 size LEB）替换为新 id+新 size LEB
    pre = d[:code['idp']]
    post = d[code['hdr'] + code['size']:]
    patched = pre + new_code + post
    print('文件 %d -> %d 字节' % (len(d), len(patched)))

    open(out, 'wb').write(patched)
    print('已写 %s' % out)

    # 回读校验
    d2 = open(out, 'rb').read()
    s2 = parse(d2)
    code2 = [s for s in s2 if s['id'] == 10][0]
    q = body_off_in_sec
    for k in range(cidx):
        b2, q = uleb(code2['body'], q); q += b2
    b2, q = uleb(code2['body'], q)
    print('回读 body#%d size=%d 起始=%s' % (cidx, b2, code2['body'][q:q + 12].hex()))
    print('名字段仍在: %s' % (b'_00oooO' in d2))


if __name__ == '__main__':
    main()
