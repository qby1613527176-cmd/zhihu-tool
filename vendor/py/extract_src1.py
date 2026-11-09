# -*- coding: utf-8 -*-
"""从 js/cvm.decompiled.js 抽出 SRC1，产出原始版与「返回 parts」的补丁版。"""
import io, re, json

src = io.open('js/cvm.decompiled.js', encoding='utf-8').read()
m = re.search(r'const SRC1 = "((?:[^"\\]|\\.)*)";', src)
if not m:
    raise SystemExit('未找到 SRC1')
js = json.loads('"' + m.group(1) + '"')
print('SRC1 长度', len(js))

TAIL = "return _0x483804(_0x24eac6[_0x93a7c4(0x209)]('|'));"
if TAIL not in js:
    # 宽松匹配
    m2 = re.search(r"return _0x483804\(_0x24eac6\[_0x93a7c4\(0x[0-9a-f]+\)\]\('\|'\)\);", js)
    if not m2:
        raise SystemExit('未找到末行；尾部 200 字符: ' + js[-200:])
    TAIL = m2.group(0)
print('末行:', TAIL)

patched = js.replace(TAIL, "return JSON.stringify(_0x24eac6);")
io.open('.probe/zsec/src1_orig.js', 'w', encoding='utf-8').write(js)
io.open('.probe/zsec/src1_parts.js', 'w', encoding='utf-8').write(patched)
print('已写 src1_orig.js / src1_parts.js')
