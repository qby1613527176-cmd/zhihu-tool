# -*- coding: utf-8 -*-
"""对比「浏览器里 SRC1 join 出来的字符串」与「我 Node 侧 join 出来的字符串」。"""
import io, hashlib, subprocess, json, sys

BROWSER = "8|Google Inc. (NVIDIA)|ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0), or similar|data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAASwAAACWCAYAAABkW7XSAAACAUlEQVR4Xu3WMQ0AAAzDsJU/6eGIZCPoFXUHELHITgDBAjo8LCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyBAvIECwgQ7CADMECMgQLyBAsIEOwgAzBAjIEC8gQLCBDsIAMwQIyHlYxAJczebf5AAAAEGRlQkc2QUZCQjc2MEE5MEFFMjVG1c8vPwAAAABJRU5ErkJggg==|Arial|Arial Black|Arial Narrow|Calibri|Cambria Math|Comic Sans MS|Consolas|Courier|Courier New|Georgia|Helvetica|Lucida Console|Lucida Sans Unicode|Microsoft Sans Serif|MS PGothic|MS Sans Serif|MS Serif|Palatino Linotype|Segoe Print|Segoe Script|Segoe UI|Segoe UI Light|Segoe UI Semibold|Tahoma|Times|Times New Roman|Trebuchet MS|Verdana|Wingdings|Wingdings 2|Wingdings 3|PDF Viewer|Chrome PDF Viewer|Chromium PDF Viewer|Microsoft Edge PDF Viewer|WebKit built-in PDF"

io.open('.probe/oracle/browser_joined.txt', 'w', encoding='utf-8').write(BROWSER)
print('浏览器 join 长度', len(BROWSER), 'md5', hashlib.md5(BROWSER.encode('utf-8')).hexdigest())

# 我这边：用 patched SRC1 拿 parts
node = r'''
const vm=require('vm'), fs=require('fs'), crypto=require('crypto');
const { createEnv } = require('./handenv.js');
const S = fs.readFileSync('.probe/zsec/src1_parts.js','utf8');
const sandbox = createEnv(); sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox, {name:'x'});
for (const k of ['Object','Array','String','Math','JSON','Function','Proxy','Reflect','Symbol','Date','RegExp','Number','Boolean','parseInt','Error','Map','Set','Uint8Array','ArrayBuffer','decodeURIComponent','unescape','encodeURIComponent']) {
  if (sandbox[k]===undefined) { try{ sandbox[k]=vm.runInContext(k,ctx);}catch(e){} }
}
sandbox.__S = S;
const parts = JSON.parse(vm.runInContext('new Function(__S)()', ctx));
const joined = parts.join('|');
fs.writeFileSync('.probe/oracle/mine_joined.txt', joined);
console.log(JSON.stringify({len: joined.length, md5: crypto.createHash('md5').update(joined,'utf8').digest('hex')}));
'''
io.open('.probe/zsec/join_mine.js','w',encoding='utf-8').write(node)
r = subprocess.run(['node', '.probe/zsec/join_mine.js'], capture_output=True, text=True, timeout=120)
print('我的 join:', r.stdout.strip(), r.stderr[:200])
