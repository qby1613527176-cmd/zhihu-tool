# -*- coding: utf-8 -*-
"""补探尚未求值的守卫（canvas / XHR / Range / NodeIterator / screen / 其它）。"""
import io, json

p = '.probe/zsec/run_build_live.js'
s = io.open(p, encoding='utf-8').read()
if 'ZSE_GUARDPROBE2' in s:
    raise SystemExit('已存在')
anchor = "  console.log('错误数:', errs.length, errs.slice(0, 3));"
if anchor not in s:
    raise SystemExit('锚点未找到')

CHECKS = [
 # payload：canvas 相关
 ('payload typeof canvas.style',      'typeof document.createElement("canvas").style === "undefined"'),
 ('payload typeof canvas.width',      'typeof document.createElement("canvas").width === "undefined"'),
 ('payload typeof canvas.height',     'typeof document.createElement("canvas").height === "undefined"'),
 ('payload typeof canvas.getContext', 'typeof document.createElement("canvas").getContext === "undefined"'),
 ('payload canvas.outerHTML idx',     'String(document.createElement("canvas").outerHTML).indexOf("canvas") === -1'),
 ('payload createElement.bind script','typeof document.createElement.bind("script") === "undefined"'),
 ('payload typeof document.all.len',  'typeof document.all.length !== "number"'),
 ('payload document.all() typeof',    'typeof (new Function("try{window={};document={};return document.all()}catch(_){}"))() !== "object"'),
 ('payload Document.all typeof',      'typeof Document.all === "undefined"'),
 # payload：screen
 ('payload typeof screen.availLeft',  'typeof screen.availLeft === "undefined"'),
 ('payload typeof screen.availTop',   'typeof screen.availTop === "undefined"'),
 ('payload typeof screen.availWidth', 'typeof screen.availWidth === "undefined"'),
 ('payload typeof screen.availHeight','typeof screen.availHeight === "undefined"'),
 ('payload typeof screen.colorDepth', 'typeof screen.colorDepth === "undefined"'),
 ('payload typeof screen.pixelDepth', 'typeof screen.pixelDepth === "undefined"'),
 # inner：XHR 实例
 ('inner typeof xhr.LOADING',   'typeof new XMLHttpRequest().LOADING === "undefined"'),
 ('inner typeof xhr.DONE',      'typeof new XMLHttpRequest().DONE === "undefined"'),
 ('inner typeof xhr.OPENED',    'typeof new XMLHttpRequest().OPENED === "undefined"'),
 ('inner typeof xhr.UNSENT',    'typeof new XMLHttpRequest().UNSENT === "undefined"'),
 ('inner typeof xhr.abort',     'typeof new XMLHttpRequest().abort === "undefined"'),
 ('inner typeof xhr.open',      'typeof new XMLHttpRequest().open === "undefined"'),
 ('inner typeof xhr.send',      'typeof new XMLHttpRequest().send === "undefined"'),
 ('inner typeof xhr.upload',    'typeof new XMLHttpRequest().upload === "undefined"'),
 ('inner typeof xhr.responseURL','typeof new XMLHttpRequest().responseURL === "undefined"'),
 ('inner typeof xhr.onreadystatechange', 'typeof new XMLHttpRequest().onreadystatechange === "undefined"'),
 ('inner typeof xhr.status',    'typeof new XMLHttpRequest().status === "undefined"'),
 ('inner typeof xhr.onabort',   'typeof new XMLHttpRequest().onabort === "undefined"'),
 ('inner typeof xhr.responseXML','typeof new XMLHttpRequest().responseXML === "undefined"'),
 # cvm：NodeIterator / Range
 ('cvm typeof iter.root',      'typeof document.createNodeIterator(document.body).root === "undefined"'),
 ('cvm typeof iter.filter',    'typeof document.createNodeIterator(document.body).filter === "undefined"'),
 ('cvm typeof iter.whatToShow','typeof document.createNodeIterator(document.body).whatToShow === "undefined"'),
 ('cvm typeof iter.detach',    'typeof document.createNodeIterator(document.body).detach === "undefined"'),
 ('cvm typeof rng.body',       'typeof document.createRange().body === "undefined"'),
 ('cvm typeof rng.root',       'typeof document.createRange().root === "undefined"'),
 ('cvm typeof rng.filter',     'typeof document.createRange().filter === "undefined"'),
 ('cvm typeof rng.detach',     'typeof document.createRange().detach === "undefined"'),
 ('cvm typeof rng.whatToShow', 'typeof document.createRange().whatToShow === "undefined"'),
 ('cvm typeof rng.cloneRange', 'typeof document.createRange().cloneRange === "undefined"'),
 ('cvm typeof rng.cloneContents','typeof document.createRange().cloneContents === "undefined"'),
 ('cvm typeof rng.collapse',   'typeof document.createRange().collapse === "undefined"'),
 ('cvm typeof rng.collapsed',  'typeof document.createRange().collapsed === "undefined"'),
 ('cvm typeof rng.commonAncestorContainer','typeof document.createRange().commonAncestorContainer === "undefined"'),
 ('cvm typeof rng.compareBoundaryPoints','typeof document.createRange().compareBoundaryPoints === "undefined"'),
 ('cvm typeof rng.comparePoint','typeof document.createRange().comparePoint === "undefined"'),
 ('cvm typeof rng.endOffset',  'typeof document.createRange().endOffset === "undefined"'),
 ('cvm typeof rng.extractContents','typeof document.createRange().extractContents === "undefined"'),
 ('cvm typeof rng.getBoundingClientRect','typeof document.createRange().getBoundingClientRect === "undefined"'),
 ('cvm typeof rng.getClientRects','typeof document.createRange().getClientRects === "undefined"'),
 ('cvm typeof rng.insertNode','typeof document.createRange().insertNode === "undefined"'),
 ('cvm typeof rng.intersectsNode','typeof document.createRange().intersectsNode === "undefined"'),
 ('cvm typeof rng.isPointInRange','typeof document.createRange().isPointInRange === "undefined"'),
 ('cvm typeof rng.selectNode','typeof document.createRange().selectNode === "undefined"'),
 ('cvm typeof rng.selectNodeContents','typeof document.createRange().selectNodeContents === "undefined"'),
 ('cvm typeof rng.setEnd','typeof document.createRange().setEnd === "undefined"'),
 ('cvm typeof rng.setEndAfter','typeof document.createRange().setEndAfter === "undefined"'),
 ('cvm typeof rng.setEndBefore','typeof document.createRange().setEndBefore === "undefined"'),
 ('cvm typeof rng.setStart','typeof document.createRange().setStart === "undefined"'),
 ('cvm typeof rng.setStartAfter','typeof document.createRange().setStartAfter === "undefined"'),
 ('cvm typeof rng.setStartBefore','typeof document.createRange().setStartBefore === "undefined"'),
 ('cvm typeof rng.startContainer','typeof document.createRange().startContainer === "undefined"'),
 ('cvm typeof rng.length',     'typeof document.createRange().length === "undefined"'),
]

CH_JSON = json.dumps(CHECKS, ensure_ascii=False)
BLOCK = (
"  /* ZSE_GUARDPROBE2 */\n"
"  try {\n"
"    const _o2 = JSON.parse(vm.runInContext('(function(){ var CH = ' + " + json.dumps(CH_JSON) + " + '; var hits = []; for (var i=0;i<CH.length;i++){ var r; try { r = eval(CH[i][1]); } catch(e){ r = \"THROW:\" + e.message; } if (r === true) hits.push(CH[i][0]); } return JSON.stringify({total: CH.length, hits: hits}); })()', ctx));\n"
"    console.log('守卫求值#2（运行上下文）: 共 ' + _o2.total + ' 条，命中 ' + _o2.hits.length + ' 条');\n"
"    _o2.hits.forEach(function(h){ console.log('   [命中2] ' + h); });\n"
"  } catch (e) { console.log('守卫求值#2 失败:', String(e.message).slice(0,140)); }\n"
)
s = s.replace(anchor, BLOCK + anchor, 1)
io.open(p, 'w', encoding='utf-8').write(s)
print('已插入守卫求值#2（%d 条）' % len(CHECKS))
