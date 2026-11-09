# -*- coding: utf-8 -*-
"""用刚抓到的「新鲜真 ck」做干净矩阵：分离「meta 有效性」与「ck↔meta 绑定」。
   cookie = '005_' + payload(128) + '-' + meta(172)，meta 取尾部 172 字符。"""
import subprocess, io, os, re, time, random, string

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"

FRESH = ("005_FpEMfFF=a9bCjWC7o40vIUshmXIA1qCNSiq0/dUMeKaejziUQt2Cxb2wR0MZzHfCbbxBIYaV7TiUSr/Sv=YyVfMMtDraxCzC1qRrGGBBkTFK0lhUop6cHC9YK1vWJ5Kq-eHfXKivMxEaSWJs/rH8b3FEXxx8luxmQnjYKSXO3Xu8bx/ZnKUk+ltULHY1fwuPdinEsBONVPcmhkHvUJyC7OGJXMFDSyyJPO2wOVkC5CB0I+Re8ezgEdKOOylW4qHcyWIMTIT4JlvXPqtgMhU0hxvt+5MAyWDobfj/ousupv+E=")

def curl(args): return subprocess.run(['curl', '-s'] + args, capture_output=True, text=True).stdout

print('fresh 长度 %d' % len(FRESH))
META_R = FRESH[-172:]
CK_R   = FRESH[:-173]          # 去掉尾部 meta 和分隔 '-'
print('切出 ck=%d  meta=%d' % (len(CK_R), len(META_R)))
print('  ck  :', CK_R[:40], '...')
print('  meta:', META_R[:40], '...')

# 另抓一个新鲜 meta（curl 侧）
h = curl(['-H', 'User-Agent: ' + UA, ACT])
M2 = re.search(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"', h).group(1)
print('另一新鲜 meta 长度 %d  (与真 meta 相同? %s)' % (len(M2), M2 == META_R))

rnd = ''.join(random.choice(string.ascii_letters + string.digits + '+/=') for _ in range(172))

MY_CK = io.open('.probe/zsec/ck_build.txt', encoding='utf-8').read().strip()

def probe(tag, cookie):
    time.sleep(6)
    out = subprocess.run(['curl', '-s', '-o', '.probe/oracle/ctrl/fresh.body', '-w', '%{http_code} %{size_download}',
                          '-H', 'User-Agent: ' + UA, '-H', 'Cookie: ' + cookie, ACT],
                         capture_output=True, text=True).stdout.strip()
    body = io.open('.probe/oracle/ctrl/fresh.body', 'rb').read().decode('utf-8', 'replace')
    jc = re.search(r'"code"\s*:\s*(\d+)', body)
    kind = '302通过' if out.startswith('302') else ('694挑战页' if '694' in out else ('40362风控' if jc and jc.group(1) == '40362' else '其他'))
    print('  %-34s %-10s code=%-6s => %s' % (tag, out, jc.group(1) if jc else '-', kind))

print('\n请求：')
probe('真ck(新) + 原配meta',        '__zse_ck=' + FRESH)
probe('真ck(新) + 另一个新鲜meta',  '__zse_ck=%s-%s' % (CK_R, M2))
probe('真ck(新) + 随机meta',        '__zse_ck=%s-%s' % (CK_R, rnd))
probe('我的ck + 真meta(新)',        '__zse_ck=%s-%s' % (MY_CK, META_R))
