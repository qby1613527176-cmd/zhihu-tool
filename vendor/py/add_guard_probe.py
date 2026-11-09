# -*- coding: utf-8 -*-
"""给 run_build_live.js 追加：在运行上下文里求值全部守卫，列出「命中」项。"""
import io, json

p = '.probe/zsec/run_build_live.js'
s = io.open(p, encoding='utf-8').read()
if 'ZSE_GUARDPROBE' in s:
    raise SystemExit('已存在')
anchor = "  console.log('错误数:', errs.length, errs.slice(0, 3));"
if anchor not in s:
    raise SystemExit('锚点未找到')

P = 'new Proxy(location,{get:function(a,b){return b in a ? a[b] : undefined;}})'
CHECKS = [
 ('inner locProxy.host',        'typeof %s.host === "undefined"' % P),
 ('inner locProxy.hostname',    'typeof %s.hostname === "undefined"' % P),
 ('inner window.devicePixelRatio',   'typeof window.devicePixelRatio === "undefined"'),
 ('inner window.blur',               'typeof window.blur === "undefined"'),
 ('inner window.blur native',        'String(window.blur).indexOf("native") === -1'),
 ('inner window.self',               'typeof window.self === "undefined"'),
 ('inner history.pushState',         'typeof history.pushState !== "function"'),
 ('inner window.XMLDocument native', 'String(window.XMLDocument).indexOf("native") === -1'),
 ('inner window.find',               'typeof window.find === "undefined"'),
 ('inner window.globalThis',         'typeof window.globalThis === "undefined"'),
 ('inner window.name',               'typeof window.name === "undefined"'),
 ('inner history.replaceState',      'typeof history.replaceState !== "function"'),
 ('payload locProxy.toString native', 'String(%s.toString).indexOf("native") === -1' % P),
 ('payload indexOf top',             'String(window.toString()).indexOf("top") === -1'),
 ('payload indexOf customElements',  'String(window.toString()).indexOf("customElements") === -1'),
 ('payload indexOf name',            'String(window.toString()).indexOf("name") === -1'),
 ('payload indexOf self',            'String(window.toString()).indexOf("self") === -1'),
 ('payload indexOf print',           'String(window.toString()).indexOf("print") === -1'),
 ('payload indexOf open',            'String(window.toString()).indexOf("open") === -1'),
 ('payload indexOf _encrypt',        'String(window.toString()).indexOf("_encrypt") === -1'),
 ('payload indexOf __g',             'String(window.toString()).indexOf("__g") === -1'),
 ('payload doc.URL zhihu',           'String(document.URL).indexOf("zhihu") === -1'),
 ('payload typeof document.all',     'typeof document.all !== "undefined"'),
 ('payload typeof document.all.len', 'typeof document.all.length !== "number"'),
 ('payload docEl HTMLHtmlElement',   'String(document.documentElement).indexOf("HTMLHtmlElement") === -1'),
 ('payload gEBTN Collection',        'String(document.getElementsByTagName("script")).indexOf("Collection") === -1'),
 ('payload baseURI zhihu',           'String(document.baseURI).indexOf("zhihu") === -1'),
 ('payload body HTMLBodyElement',    'String(document.body).indexOf("HTMLBodyElement") === -1'),
 ('payload ctx2d CanvasRenderingContext2D', 'String(document.createElement("canvas").getContext("2d")).indexOf("CanvasRenderingContext2D") === -1'),
 ('payload canvas.outerHTML',        'String(document.createElement("canvas").outerHTML).indexOf("canvas") === -1'),
 ('payload typeof document.baseURI', 'typeof document.baseURI === "undefined"'),
 ('payload typeof document.charset', 'typeof document.charset === "undefined"'),
 ('payload typeof document.body',    'typeof document.body === "undefined"'),
 ('payload typeof localStorage.setItem', 'typeof localStorage.setItem === "undefined"'),
 ('payload typeof Document.all',     'typeof Document.all === "undefined"'),
 ('payload typeof window.buffer',    'typeof window.buffer === "undefined"'),
 ('payload typeof window.emit',      'typeof window.emit === "undefined"'),
 ('payload typeof window.URL',       'typeof window.URL === "undefined"'),
 ('payload typeof window.stop',      'typeof window.stop === "undefined"'),
 ('payload typeof screen.availLeft', 'typeof screen.availLeft === "undefined"'),
 ('payload typeof screen.availHeight', 'typeof screen.availHeight === "undefined"'),
 ('payload typeof screen.colorDepth', 'typeof screen.colorDepth === "undefined"'),
 ('payload typeof screen.pixelDepth', 'typeof screen.pixelDepth === "undefined"'),
]
for nm in ['callPhantom','__phantomas','_phantom','WebPage','fxdriver_id','__fxdriver_unwrapped','ubot',
           'CasperError','casper','patchRequire','$cdc_asdjflasutopfhvcZLmcfl_','webdriver',
           '__webdriver_script_fn','_resourceLoader','_sessionHistory','_virtualConsole']:
    CHECKS.append(('payload typeof window.%s' % nm, 'typeof window["%s"] === "undefined"' % nm))

CH_JSON = json.dumps(CHECKS, ensure_ascii=False)
BLOCK = (
"  /* ZSE_GUARDPROBE */\n"
"  try {\n"
"    const _out = vm.runInContext('(function(){ var CH = ' + " + json.dumps(CH_JSON) + " + '; var hits = []; for (var i=0;i<CH.length;i++){ var r; try { r = eval(CH[i][1]); } catch(e){ r = \"THROW:\" + e.message; } if (r === true) hits.push(CH[i][0]); } return JSON.stringify({total: CH.length, hits: hits}); })()', ctx);\n"
"    const _o = JSON.parse(_out);\n"
"    console.log('守卫求值（运行上下文）: 共 ' + _o.total + ' 条，命中 ' + _o.hits.length + ' 条');\n"
"    _o.hits.forEach(function(h){ console.log('   [命中] ' + h); });\n"
"  } catch (e) { console.log('守卫求值失败:', String(e.message).slice(0,140)); }\n"
)
s = s.replace(anchor, BLOCK + anchor, 1)
io.open(p, 'w', encoding='utf-8').write(s)
print('已插入守卫求值探针（%d 条）' % len(CHECKS))
