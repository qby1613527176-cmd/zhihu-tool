import re, sys
p = sys.argv[1] if len(sys.argv)>1 else '.probe/zsec/mem_build.bin'
mem = open(p,'rb').read()
pat = re.compile(rb'[0-9a-f]{48}[0-9a-f]{32}z[0-9a-f]{12}')
seen = []
for m in pat.finditer(mem):
    s = m.group().decode()
    if s in seen: continue
    seen.append(s)
for s in seen:
    bm = s[:48]
    print('record93 @0x%x' % mem.find(s.encode()))
    print('  位图  : ' + ' '.join(bm[i:i+4] for i in range(0,48,4)))
    print('  指纹  : ' + s[48:80])
    print('  尾    : ' + s[80:])
if not seen:
    print('未找到 record93（93 字符）')