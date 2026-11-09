# -*- coding: utf-8 -*-
"""对照实验：伪造 cookie 与我的 cookie 分别得到什么响应类别。
判据：403/694B = 挑战页（未解析 token）；403/214B+code40362 = 风控（token 被解析）；
      200 = 通过。"""
import subprocess, io, os, re, json, time, random, base64

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
URL = "https://www.zhihu.com/people/zhihuadmin/activities"
OUT = '.probe/oracle/ctrl'
os.makedirs(OUT, exist_ok=True)

def rnd_b64(n):
    return base64.b64encode(bytes(random.getrandbits(8) for _ in range(n))).decode().replace('=', '')[:n]

mine = io.open('.probe/zsec/ck_build.txt', encoding='utf-8').read().strip()
meta_fresh = io.open('.probe/oracle/meta2.txt', encoding='utf-8').read().strip()
meta_old = io.open('.probe/oracle/meta.txt', encoding='utf-8').read().strip()

CASES = [
    ('none',              None),
    ('garbage_short',     '005_' + rnd_b64(20)),
    ('garbage_len132',    '005_' + rnd_b64(128)),
    ('garbage_132_meta',  '005_' + rnd_b64(128) + '-' + meta_fresh),
    ('mine_nometa',       mine),
    ('mine_freshmeta',    mine + '-' + meta_fresh),
    ('mine_oldmeta',      mine + '-' + meta_old),
    ('allzeros_132_meta', '005_' + 'A' * 128 + '-' + meta_fresh),
]

rows = []
for tag, cookie in CASES:
    time.sleep(6)
    hf = os.path.join(OUT, tag + '.headers'); bf = os.path.join(OUT, tag + '.body')
    cmd = ['curl', '-s', '-D', hf, '-o', bf, '-w', '%{http_code} %{size_download}',
           '-H', 'User-Agent: ' + UA,
           '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
           '-H', 'Accept-Language: zh-CN,zh;q=0.9']
    if cookie:
        cmd += ['-H', 'Cookie: __zse_ck=' + cookie]
    cmd.append(URL)
    r = subprocess.run(cmd, capture_output=True, text=True)
    txt = io.open(bf, 'rb').read().decode('utf-8', 'replace')
    code = None
    m = re.search(r'"code"\s*:\s*(\d+)', txt)
    if m: code = m.group(1)
    has_meta = '<meta id="zh-zse-ck"' in txt
    rows.append({'tag': tag, 'ck_len': len(cookie) if cookie else 0,
                 'resp': r.stdout.strip(), 'json_code': code,
                 'challenge_page': has_meta, 'head': txt[:90].replace('\n', ' ')})
    print('%-20s ck=%-4s  %-10s code=%-6s challenge=%s' % (
        tag, rows[-1]['ck_len'], rows[-1]['resp'], code, has_meta))

io.open('.probe/oracle/ctrl_matrix.json', 'w', encoding='utf-8').write(
    json.dumps(rows, ensure_ascii=False, indent=1))
