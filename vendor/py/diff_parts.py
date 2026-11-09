# -*- coding: utf-8 -*-
"""对比真浏览器 parts 与 handenv parts，并给出 md5(parts.join('|')) 差异。"""
import io, json, hashlib

REAL = {
 "parts": [8, "Google Inc. (NVIDIA)",
  "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0), or similar",
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAASwAAACWCAYAAABkW7XSAAACAUlEQVR4Xu3WMQ0AAAzDsJU/6eGIZCPoFXUHELHITgDBAjo8LCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyHlYxAJczebf5AAAAEGRlQkc2QUZCQjc2MEE5MEFFMjVG1c8vPwAAAABJRU5ErkJggg==",
  "Arial","Arial Black","Arial Narrow","Calibri","Cambria Math","Comic Sans MS","Consolas","Courier",
  "Courier New","Georgia","Helvetica","Lucida Console","Lucida Sans Unicode","Microsoft Sans Serif",
  "MS PGothic","MS Sans Serif","MS Serif","Palatino Linotype","Segoe Print","Segoe Script","Segoe UI",
  "Segoe UI Light","Segoe UI Semibold","Tahoma","Times","Times New Roman","Trebuchet MS","Verdana",
  "Wingdings","Wingdings 2","Wingdings 3","PDF Viewer","Chrome PDF Viewer","Chromium PDF Viewer",
  "Microsoft Edge PDF Viewer","WebKit built-in PDF"],
 "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"}

mine = json.load(io.open('.probe/oracle/mine_parts.json', encoding='utf-8'))
io.open('.probe/oracle/real_parts.json', 'w', encoding='utf-8').write(json.dumps(REAL, ensure_ascii=False, indent=1))

def md5(parts): return hashlib.md5('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()

print('真 parts 数 %d  md5=%s' % (len(REAL['parts']), md5(REAL['parts'])))
print('我 parts 数 %d  md5=%s' % (len(mine['parts']), md5(mine['parts'])))
print('UA 一致:', REAL['ua'] == mine['ua'])
print()
rp, mp = REAL['parts'], mine['parts']
n = max(len(rp), len(mp))
for i in range(n):
    r = rp[i] if i < len(rp) else '<缺>'
    m = mp[i] if i < len(mp) else '<缺>'
    if r != m:
        rs = (r[:90] + '…(%d)' % len(r)) if isinstance(r, str) and len(r) > 90 else r
        ms = (m[:90] + '…(%d)' % len(m)) if isinstance(m, str) and len(m) > 90 else m
        print('[%2d] 真: %s' % (i, rs))
        print('     我: %s' % ms)
print('\n只在我这边出现的:', [x for x in mp if x not in rp])
print('只在真那边出现的:', [x for x in rp if x not in mp])
