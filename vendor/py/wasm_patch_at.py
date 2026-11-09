# -*- coding: utf-8 -*-
"""在 wasm 里任意字节偏移处插 fd_write 断点（wasm 从 build JS 的内嵌 base64 里提）。

用法: python py/wasm_patch_at.py <build.js> <out.wasm> <hex_offset> [localIdx]
  localIdx 默认 1 ⇒ 插 `local.get <localIdx>; local.get 0; 0; 0; call fd_write; drop`
  ⇒ JS 侧 fd_write 的 fd 参数 = 该 local 的值（用于抓「正在被格式化的值」）。

插入本体与 raw 版共用，见 `py/wasm_patch_raw.py` 的 `patch()`。
"""
import io, os, re, sys, base64

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wasm_patch_raw import patch, uleb, leb  # noqa: F401  (leb/uleb 供外部 import)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def extract_wasm(js_path):
    s = io.open(js_path, 'r', encoding='utf-8', errors='replace').read()
    for m in re.finditer(r'[A-Za-z0-9+/]{200,}={0,2}', s):
        b64 = m.group()
        try:
            d = base64.b64decode(b64 + '=' * ((4 - len(b64) % 4) % 4))
        except Exception:
            continue
        if d[:4] == b'\x00asm':
            return d
    return None


def main():
    js = sys.argv[1] if len(sys.argv) > 1 else '.probe/oracle/live_clean.js'
    out = sys.argv[2] if len(sys.argv) > 2 else '.probe/vmsvc/wasm_fmt.wasm'
    off = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0xF4AC
    loc = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    js = js if os.path.isabs(js) else os.path.join(ROOT, js)
    out = out if os.path.isabs(out) else os.path.join(ROOT, out)

    d = extract_wasm(js)
    if d is None:
        print('在 %s 里找不到内嵌 wasm' % js)
        return 1
    patched = patch(d, off, [loc, 0, 0])
    if patched is None:
        return 1
    io.open(out, 'wb').write(patched)
    print('文件 %d -> %d 字节；插在 0x%X（local %d）' % (len(d), len(patched), off, loc))
    return 0


if __name__ == '__main__':
    sys.exit(main())
