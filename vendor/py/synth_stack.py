# -*- coding: utf-8 -*-
"""把 run_build_live.js 里上下文内的 prepareStackTrace 换成「整段合成 Firefox 16 帧模板」。"""
import io

p = '.probe/zsec/run_build_live.js'
s = io.open(p, encoding='utf-8').read()

start = s.find('// Firefox 形态的 Error.stack（在上下文内部设 prepareStackTrace）')
if start < 0:
    raise SystemExit('未找到注入块')
end = s.find('// 记录所有 wasm 宿主调用', start)
if end < 0:
    raise SystemExit('未找到结束锚点')

# 真浏览器 zh 第2段解码出来的 16 帧模板（URL 占位 @@U@@）
TEMPLATE = [
    'CVM/this.z', 'CVM/this.z', '', 'BVM/this.z', 'BVM/this.z', '', 'VM/this.z', 'VM/this.z',
    'VM/this.z/this.A[3]', 'syscall/js.copyBytesToGo', '(syscall/js.Value).Call',
    '(*main._0oOoOO)._00oooO', '(*main._0oOoOO)._0Ooo00$bound', '_start.command_export',
    'window.Go/this.run', '',
]
SUFFIX = [
    ' line 1 > eval line 1 > eval:1:15604',
    ' line 1 > eval line 1 > eval:1:43686',
    ' line 1 > eval line 1 > eval:1:47750',
    ' line 1 > eval:1:27668',
    ' line 1 > eval:1:30377',
    ' line 1 > eval:1:135996',
    ':1:42224',
    ':1:53679',
    ':1:49364',
    ' line 1 > eval:1:8271',
    ' line 1 > WebAssembly.instantiate:wasm-function[186]:0xde22',
    ' line 1 > WebAssembly.instantiate:wasm-function[188]:0xf9fc',
    ' line 1 > WebAssembly.instantiate:wasm-function[61]:0x6cde',
    ' line 1 > WebAssembly.instantiate:wasm-function[199]:0x1399d',
    ' line 1 > eval:1:8877',
    ':1:522024',
]

lines = []
for fn, sfx in zip(TEMPLATE, SUFFIX):
    lines.append("      '" + fn + "@' + U + '" + sfx + "',")
body = '\n'.join(lines)

NL = chr(92) + 'n'
BLOCK = """// Firefox 形态的 Error.stack：按真浏览器 zh 第2段的 16 帧模板**整段合成**
//   （Firefox 的 Error().stack 没有 "Error" 前缀行；每帧都带构建脚本真实 URL）
vm.runInContext(`
  (function(){
    var NL = String.fromCharCode(10);
    var U = ${JSON.stringify(process.env.BUILD_URL || 'build.js')};
    var LINES = [
@@BODY@@
    ];
    var TEMPLATE = LINES.join(NL) + NL;
    function pst(err, frames){ return TEMPLATE; }
    try { Object.defineProperty(Error, 'prepareStackTrace', { value: pst, configurable: true, writable: true }); } catch(e){}
  })();
`, ctx);

""".replace('@@BODY@@', body).replace('@@NL@@', NL)

s = s[:start] + BLOCK + s[end:]
io.open(p, 'w', encoding='utf-8').write(s)
print('已替换为整段合成模板（%d 帧）' % len(TEMPLATE))
