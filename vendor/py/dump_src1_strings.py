# -*- coding: utf-8 -*-
"""把 SRC1 改成返回它的字符串解码器 _0x4848，然后打印代码里用到的各索引的真实字符串。"""
import io, re, json, subprocess

js = io.open('.probe/zsec/src1_orig.js', encoding='utf-8').read()
# 在末尾追加：把内部解码器暴露出去
patched = js.replace('return r();', 'try{globalThis.__DEC = _0x4848;}catch(e){};return r();')
if patched == js:
    raise SystemExit('未找到 return r();')
io.open('.probe/zsec/src1_dec.js', 'w', encoding='utf-8').write(patched)

IDX = [0x209, 0x1fd, 0x1b9, 0x1ee, 0x205, 0x1c9, 0x1bd, 0x213, 0x214, 0x218, 0x1dc,
       0x1a9, 0x1e7, 0x1a4, 0x1a3, 0x1fe, 0x1b7, 0x21c, 0x1cc, 0x1c3, 0x1d4, 0x1b2,
       0x20b, 0x1d5, 0x1a1, 0x1dd, 0x216, 0x1e5, 0x1ad, 0x1bb, 0x1a6, 0x1eb, 0x201,
       0x1ca, 0x200, 0x1e2, 0x1c1, 0x21b, 0x1b6, 0x1a0, 0x207, 0x1cd, 0x1bc, 0x21e,
       0x1c4, 0x1ba, 0x219, 0x211, 0x1ce, 0x1be, 0x1f3, 0x20a, 0x1ec]
node = r'''
const vm=require('vm'), fs=require('fs');
const { createEnv } = require('./handenv.js');
const S = fs.readFileSync('.probe/zsec/src1_dec.js','utf8');
const sandbox = createEnv(); sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox, {name:'d'});
for (const k of ['Object','Array','String','Math','JSON','Function','Proxy','Reflect','Symbol','Date','RegExp','Number','Boolean','parseInt','Error','Map','Set','Uint8Array','ArrayBuffer','decodeURIComponent','unescape','encodeURIComponent']) {
  if (sandbox[k]===undefined) { try{ sandbox[k]=vm.runInContext(k,ctx);}catch(e){} }
}
sandbox.__S = S;
try { vm.runInContext('new Function(__S)()', ctx); } catch(e) { console.log('执行异常:', e.message); }
const idx = %s;
const out = {};
for (const i of idx) {
  try { out['0x'+i.toString(16)] = String(vm.runInContext('globalThis.__DEC('+i+')', ctx)); } catch(e) { out['0x'+i.toString(16)] = 'ERR '+e.message; }
}
console.log(JSON.stringify(out, null, 1));
''' % json.dumps(IDX)
io.open('.probe/zsec/dec_dump.js', 'w', encoding='utf-8').write(node)
r = subprocess.run(['node', '.probe/zsec/dec_dump.js'], capture_output=True, text=True, timeout=120)
print(r.stdout[:4000])
if r.stderr: print('STDERR:', r.stderr[:800])
