# -*- coding: utf-8 -*-
import io, base64, json

Z = 0x200C

def show(name, path):
    try:
        s = io.open(path, encoding='utf-8', errors='replace').read()
    except Exception as e:
        print(name, 'ERR', e); return
    print('===', name, 'len=', len(s))
    segs = s.split(chr(Z))
    print('  segments:', len(segs), 'lens:', [len(x) for x in segs])
    for i, seg in enumerate(segs):
        head = seg[:70]
        extra = ''
        # try base64
        try:
            b = base64.b64decode(seg + '=' * (-len(seg) % 4))
            txt = b.decode('utf-8', 'replace')
            if sum(1 for c in txt if c.isprintable()) > len(txt) * 0.85:
                extra = ' | B64->' + repr(txt[:220])
        except Exception:
            pass
        print('   [%d] len=%d %r%s' % (i, len(seg), head, extra))

show('REAL zh', '.probe/zsec/generated_zh.txt')
show('MINE zh', '.probe/zsec/zh_mine_full.txt')
show('MINE qh', '.probe/zsec/qh_mine_full.txt')
