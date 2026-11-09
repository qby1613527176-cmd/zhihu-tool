# -*- coding: utf-8 -*-
"""真 ck × 各种 meta 的组合实验：判定 meta 与 ck 的绑定性质。"""
import subprocess, io, os, re, time, random, string

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"

REAL_CK = ("005_rebdvSrc60HFQmZymBS2DH5yUSyahj9vuyEEoMNLYsOnetshd7tug1d86KuUvKvBDb522XvyuaVnKg8a4pIeNH4pIygki"
           "M4xPOviIqHtNJ7cQb1pxaWcocSlzrnaKVVx")
REAL_META = ("9tOv1xOxhFl+tw8Y+aaEnTJqDrFdcB5zKCCn+f4xqCHjXMhyd0LnfZ8evkcZ1qzTluOVTC9oOk71WuShwgbYS7f3fUWcbHc3CG+3"
             "/XapdzcDtLQI53hb1ZBPZJ1ayU7E+Z6Nn70jVMpWC9vC8uxGn7sy2zhYvn2rI/FTSDdhoVs=")
MY_CK = io.open('.probe/zsec/ck_build.txt', encoding='utf-8').read().strip()
MY_META = io.open('.probe/oracle/meta2.txt', encoding='utf-8').read().strip()

def rnd_meta(n=172):
    alpha = string.ascii_letters + string.digits + '+/='
    return ''.join(random.choice(alpha) for _ in range(n))

# 把真 meta 改一个字符（保持长度/字符集）
mut = list(REAL_META); mut[10] = 'A' if mut[10] != 'A' else 'B'; MUT_META = ''.join(mut)

CASES = [
    ('real_ck + real_meta',     REAL_CK, REAL_META),
    ('real_ck + other_real_meta', REAL_CK, MY_META),
    ('real_ck + random172',     REAL_CK, rnd_meta(172)),
    ('real_ck + meta1char_mut', REAL_CK, MUT_META),
    ('real_ck + short20',       REAL_CK, rnd_meta(20)),
    ('real_ck + no_meta',       REAL_CK, None),
    ('my_ck + random172',       MY_CK,   rnd_meta(172)),
    ('my_ck + no_meta',         MY_CK,   None),
]

print('real_meta 长度 %d, my_meta 长度 %d' % (len(REAL_META), len(MY_META)))
for tag, ck, meta in CASES:
    time.sleep(6)
    cookie = ck if meta is None else (ck + '-' + meta)
    out = subprocess.run(['curl', '-s', '-o', '.probe/oracle/ctrl/bind.body', '-w', '%{http_code} %{size_download}',
                          '-H', 'User-Agent: ' + UA, '-H', 'Cookie: __zse_ck=' + cookie, ACT],
                         capture_output=True, text=True).stdout.strip()
    body = io.open('.probe/oracle/ctrl/bind.body', 'rb').read().decode('utf-8', 'replace')
    jc = re.search(r'"code"\s*:\s*(\d+)', body)
    kind = '302通过' if out.startswith('302') else ('694挑战页' if '694' in out else ('40362风控' if jc and jc.group(1) == '40362' else '其他'))
    print('%-26s %-10s code=%-6s => %s' % (tag, out, jc.group(1) if jc else '-', kind))
