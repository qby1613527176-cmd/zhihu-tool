# -*- coding: utf-8 -*-
"""差分法定位 w8 bit11：在真机（Edge）里逐项删环境特征，看位图哪一位变。
思路：两个真机的 w8 都是 8000、只有我的环境是 8200 ⇒ 是环境偏差 ⇒ 在真机里删对了就会复现 8200。
"""
import base64, io, json, os, re, sys, time
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACT = "https://www.zhihu.com/people/zhihuadmin/activities"

HOOK = r"""
window.__INST = null;
(function(){
  function grab(r){ try { window.__INST = (r && r.instance) ? r.instance : r; } catch(e){} return r; }
  ['instantiate','instantiateStreaming'].forEach(function(n){
    var o = WebAssembly[n]; if (typeof o !== 'function') return;
    WebAssembly[n] = function(){ var r = o.apply(this, arguments);
      if (r && typeof r.then === 'function') return r.then(grab); return grab(r); };
  });
  window.__findMem = function(){
    var seen = new Set(), best = null;
    function scan(o, d){
      if (!o || d > 2 || best) return;
      var t = typeof o; if (t !== 'object' && t !== 'function') return;
      if (seen.has(o)) return; seen.add(o);
      var ks; try { ks = Object.getOwnPropertyNames(o); } catch(e){ return; }
      for (var i = 0; i < ks.length && i < 60; i++) {
        var v; try { v = o[ks[i]]; } catch(e){ continue; }
        if (v && typeof v === 'object') {
          var b; try { b = v.buffer; } catch(e){}
          if (b && typeof b.byteLength === 'number' && b.byteLength > 1000000 && b.byteLength % 65536 === 0) { best = v; return; }
          scan(v, d + 1);
        }
      }
    }
    scan(window, 0);
    return best;
  };
  window.__grabB64 = function(){
    var m = (window.__INST && window.__INST.exports && (window.__INST.exports.mem || window.__INST.exports.memory)) || window.__findMem();
    if (!m) return null;
    var u8 = new Uint8Array(m.buffer), s = '';
    for (var i = 0; i < u8.length; i += 65536) s += String.fromCharCode.apply(null, u8.subarray(i, Math.min(i + 65536, u8.length)));
    return btoa(s);
  };
  try {
    var _d = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie') ||
             Object.getOwnPropertyDescriptor(HTMLDocument.prototype, 'cookie');
    Object.defineProperty(document, 'cookie', {
      configurable: true,
      get: function(){ return _d.get.call(document); },
      set: function(v){
        try { if (!window.__PUSHED && String(v).indexOf('__zse_ck') >= 0) {
          window.__PUSHED = 1;
          window.__zse_dump({ b64: window.__grabB64(), ck: String(v).slice(0, 30) });
        } } catch(e){}
        return _d.set.call(document, v);
      },
    });
  } catch(e) {}
})();
"""

# 扰动脚本（在页面脚本之前跑）
PERTURB = {
    'baseline': "",
    'no_document_all': "try{Object.defineProperty(document,'all',{get:function(){return undefined;},configurable:true});}catch(e){}",
    'xhr_no_unsent': "try{delete XMLHttpRequest.UNSENT;}catch(e){}",
    'xhr_no_proto_members': "try{['abort','send','upload','responseURL','status','responseXML','onreadystatechange','open','setRequestHeader'].forEach(function(k){delete XMLHttpRequest.prototype[k];});}catch(e){}",
    'no_hostname': "try{delete Location.prototype.hostname;Object.defineProperty(location,'hostname',{get:function(){return undefined;},configurable:true});}catch(e){}",
    'no_blur': "try{delete Window.prototype.blur;Object.defineProperty(window,'blur',{value:undefined,configurable:true});}catch(e){}",
    'no_deviceratio': "try{Object.defineProperty(window,'devicePixelRatio',{get:function(){return undefined;},configurable:true});}catch(e){}",
    'no_range_methods': "try{['getClientRects','insertNode','intersectsNode','isPointInRange','selectNode'].forEach(function(k){delete Range.prototype[k];});}catch(e){}",
    'no_ni_filter': "try{var _c=document.createNodeIterator;document.createNodeIterator=function(){var r=_c.apply(document,arguments);try{delete r.filter;}catch(e){}return r;};}catch(e){}",
    'no_find': "try{delete Window.prototype.find;Object.defineProperty(window,'find',{value:undefined,configurable:true});}catch(e){}",
    'no_xmldoc': "try{delete Window.prototype.XMLDocument;Object.defineProperty(window,'XMLDocument',{value:undefined,configurable:true});}catch(e){}",
}


def one(name, code):
    DUMP = {'b64': None, 'ck': None}
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="msedge", headless=False,
                               args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(locale="zh-CN")
        def _on(source, payload):
            if isinstance(payload, dict):
                DUMP['b64'] = payload.get('b64'); DUMP['ck'] = payload.get('ck')
        ctx.expose_binding('__zse_dump', _on)
        ctx.add_init_script(HOOK)
        if code:
            ctx.add_init_script(code)
        pg = ctx.new_page()
        try:
            pg.goto(ACT, wait_until="load", timeout=60000)
            pg.wait_for_timeout(16000)
            for _ in range(8):
                try:
                    u1 = pg.url; pg.wait_for_timeout(2000)
                    if pg.url == u1: break
                except Exception:
                    pg.wait_for_timeout(2000)
        except Exception as e:
            print('  [%s] 导航异常 %s' % (name, str(e)[:60]))
        try: b.close()
        except Exception: pass
    if not DUMP['b64']:
        return None
    raw = base64.b64decode(DUMP['b64'])
    m = re.search(rb'[\x20-\x7e]{48}[0-9a-f]{32}z[0-9a-f]{12}', raw)
    return m.group().decode() if m else None


def main():
    names = sys.argv[1:] if len(sys.argv) > 1 else list(PERTURB)
    for nm in names:
        if nm not in PERTURB: continue
        t0 = time.time()
        r = one(nm, PERTURB[nm])
        if not r:
            print('%-24s [%4.0fs] 未采到' % (nm, time.time() - t0), flush=True); continue
        bm = ' '.join(r[i:i + 4] for i in range(0, 48, 4))
        print('%-24s [%4.0fs] %s' % (nm, time.time() - t0, bm), flush=True)


if __name__ == '__main__':
    main()
