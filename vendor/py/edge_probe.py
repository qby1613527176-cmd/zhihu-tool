# -*- coding: utf-8 -*-
"""用真机 Edge（playwright channel=msedge）跑挑战页，hook WebAssembly.instantiate 抓 wasm 内存，
提取 record93，与 Firefox / 我的环境对照位图。"""
import base64, io, json, os, re, sys, time
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"

INIT = r"""
window.__INST = null;
(function(){
  var _o = WebAssembly.instantiate;
  WebAssembly.instantiate = function(b, i){
    var p = _o.apply(this, arguments);
    function grab(r){ try { window.__INST = r.instance ? r.instance : r; } catch(e){} return r; }
    if (p && typeof p.then === 'function') return p.then(grab);
    return grab(p);
  };
})();
"""


def main():
    t0 = time.time()
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="msedge", headless=False,
                               args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(locale="zh-CN",
                            user_agent=None)
        ctx.add_init_script(INIT)
        pg = ctx.new_page()
        pg.goto(ACT, wait_until="load", timeout=60000)
        print('[%5.1fs] 已打开 %s  status=%s' % (time.time() - t0, pg.url, '?'))
        pg.wait_for_timeout(15000)
        info = pg.evaluate("""() => {
          const m = document.querySelector('meta#zh-zse-ck');
          const ck = (document.cookie.match(/__zse_ck=([^;]*)/) || [])[1] || null;
          return { url: location.href, title: document.title,
                   hasMeta: !!m, ckLen: ck ? ck.length : 0, ckHead: ck ? ck.slice(0, 32) : null,
                   hasInst: !!(window.__INST && window.__INST.exports) };
        }""")
        print('[%5.1fs] %s' % (time.time() - t0, json.dumps(info, ensure_ascii=False)[:300]))

        mem = pg.evaluate("""() => {
          const I = window.__INST;
          if (!I || !I.exports) return null;
          const m = I.exports.mem || I.exports.memory;
          if (!m) return null;
          const u8 = new Uint8Array(m.buffer);
          let s = '';
          const CH = 65536;
          for (let i = 0; i < u8.length; i += CH) {
            s += String.fromCharCode.apply(null, u8.subarray(i, Math.min(i + CH, u8.length)));
          }
          return btoa(s);
        }""")
        if not mem:
            print('!! 没拿到 wasm 内存'); b.close(); return
        raw = base64.b64decode(mem)
        print('[%5.1fs] wasm 内存 %d 字节' % (time.time() - t0, len(raw)))
        io.open(os.path.join(ROOT, '.probe/zsec/mem_edge.bin'), 'wb').write(raw)

        ck = pg.evaluate("() => (document.cookie.match(/__zse_ck=([^;]*)/) || [])[1] || null")
        if ck:
            io.open(os.path.join(ROOT, '.probe/zsec/ck_edge.txt'), 'w', encoding='utf-8').write(ck)
        b.close()

    recs = set(m.group().decode() for m in re.finditer(rb'[\x20-\x7e]{48}[0-9a-f]{32}z[0-9a-f]{12}', raw))
    print('\n=== Edge 内存里的 record93 ===')
    for r in sorted(recs):
        print('  %s' % r)
        print('   位图 = %s' % ' '.join(r[i:i + 4] for i in range(0, 48, 4)))
    toks = set(m.group().decode() for m in re.finditer(rb'005_[A-Za-z0-9+/=]{128}', raw))
    print('token 数 =', len(toks))
    for t in sorted(toks)[:2]:
        print('  ', t[:60])
    print('\n对照：Firefox 真机位图 = 36c0 2000 1000 8000 4030 8000 8000 8800 8000 8000 8000 4000')
    print('      我的环境位图     = 2000 1000 8000 4030 8000 8000 8800 8200 8000 8000 4000 (除 w0)')


if __name__ == '__main__':
    main()
