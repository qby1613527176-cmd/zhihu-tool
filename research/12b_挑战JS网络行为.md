# 12b · zse_ck 挑战 JS 网络行为分析（遥测/验证请求 + 服务端「解题档案」机制）

> 日期：2026-11-06 ｜ 方法：本机 8 份挑战 JS 缓存静态分析 + WASM 字符串提取 + 真实挑战页活捉 + 开源对照（RSSHub PR#22319 / zhihu-proxy / fxzhihu PR#64·65）
> 样本：`/tmp/zse_ck_js_*.js`（8 份，521.7–522.1 KB，md5 两两不同）；真实挑战页 `/tmp/challenge_live.html`（2026-11-06 06:4x 活捉）

---

## TL;DR（一行结论）

**挑战 JS 全程只可能发出 2 种 XHR——且都是「出错才发」的稳定性监控（POST `https://www.zhihu.com/sc-assets-tracker` + SRI 复检 GET），没有任何「解题成功上报」；服务端信任 cookie 靠的是签发种子 + 每份个性化 WASM 的 PoW 校验，不需要遥测报告；40362 与遥测无关（UA/签名/cookie 一致性/IP 声誉维度）。**

---

## ① 挑战 JS 求解后向哪个端点报告？

### 端点清单（8 份缓存逐一核对，结果完全一致）

| # | 端点 | 方法 | 触发条件 | 载荷 | 正常求解会发吗 |
|---|------|------|----------|------|----------------|
| 1 | `https://www.zhihu.com/sc-assets-tracker` | POST XHR，`Content-Type: application/json;charset=UTF-8` | **仅错误路径**：SRI 校验失败 / 资源加载失败 / 资产超时 / 外部脚本 JS 运行时错误 | 见下方结构 | **不出错就不发** |
| 2 | `<失败资源的 URL>`（如挑战 JS 自身的 static.zhihu.com 地址） | GET XHR（带 `Cache-Control: no-cache` 复检） | 仅当 SCRIPT/LINK 元素带 `integrity` 属性且触发 error 事件后，二次拉取确认（"retouch"） | 无 body | **不出错就不发** |

**除此之外：零。** 全部 8 份 JS 中 `fetch`=0、`sendBeacon`=0、`new Image`=0、WebSocket/EventSource=0；除 sc-assets-tracker 外无任何其他域名/路径字符串（`.zhihu.com` 仅出现在 cookie 的 `domain=.zhihu.com` 属性里）。内嵌 WASM（335 KB，TinyGo 0.31.2 编译）提取全部可见字符串：`fetch`/`XMLHttpRequest`/`cookie`/`document`/`navigator`/`http` 等浏览器 API 名 **全部 0 命中**——WASM 是纯计算 PoW，不碰网络。

### sc-assets-tracker 载荷结构（原文逆向）

 tracker 模块配置来自挑战页 script 标签的 `data-assets-tracker-config`（活捉实测原值）：

```html
<script data-assets-tracker-config='{"appName":"zse_ck","trackJSRuntimeError":true}'
        crossorigin="" src="https://static.zhihu.com/zse-ck/v4/<sha256>.js">
```

→ 解析后：`appName="zse_ck"`，`trackJSRuntimeError=true`，`assets=[]`（空！），`isHybrid=false`，`artifactTag/spanName="unknown"`，`maxTimeout=25s`。

两种载荷：

```jsonc
// A. 资源错误（error.type="resource"）——name ∈ {sri-mismatch, retouch-ok, retouch-empty,
//     retouch-error-<status>, retouch-unknown-error, perf-timeout}
{"artifactTag":"unknown","spanName":"unknown","appName":"zse_ck",
 "error":{"type":"resource","name":"<见上>","assets":["<资源URL>"]}}

// B. JS 运行时错误（error.type="js"，trackJSRuntimeError=true 时武装）
{"artifactTag":"unknown","spanName":"unknown","appName":"zse_ck",
 "error":{"type":"js","name":"RuntimeError","message":"...","stack":"...(换行符已剔除)","filename":"<脚本URL>"}}
// 注意：filename === window.location.href 的错误被过滤（只报外部脚本）
```

**载荷里没有任何能关联到具体挑战的字段**——无种子、无 d_c0、无 cookie 值、无会话 ID。关联只能靠 IP+UA+时间+filename（JS hash）侧面推断。

### 为什么正常流程基本不发

tracker 的全部发送函数都被 `e()` 包装器（`try{...}catch{}` 静默吞错）包裹，且触发条件全是错误事件：

- `assets=[]` → 25s 超时巡检 `perf-timeout` 恒空，不发；
- 挑战页 script 标签实测 **无 integrity 属性** → SRI/retouch 路径不激活；
- `isHybrid` 缺省 false → 混合栈监控 `j()`（另一套 PerformanceObserver/MutationObserver 资产监控）不运行；
- 唯一可能常态触发的是 `window.addEventListener("error", g)`：若求解过程向 window 抛出未捕获错误（filename=挑战 JS URL≠location.href），会 POST 一条 B 型载荷。**已知候选**：Go WASM 环境探测分支的 `RuntimeError: unreachable` 陷阱（RSSHub PR#22319 实锤为正常路径）——但它若走 Promise rejection 通道则触发 `unhandledrejection` 而非 `error` 事件，tracker 不监听前者。**是否常态上报无法纯静态确定，标记为「可能」**（见 ② 的对照证据，即使常态上报也不构成信任门槛）。

### 求解完成后的真实动作流（尾部代码原文）

```js
WebAssembly.instantiate(new Uint8Array(Array.from(atob("<335KB base64>"), c=>c.charCodeAt(0))).buffer, _g._)
  .then(k => { _g.run(k.instance); sc(); })
  .catch(_ => sc());   // 失败也写 cookie（回退值）
// 外层 else/catch 同样兜底 sc()

function sc(){
  var e="__zse_ck",
      t=(typeof __g.ck=="string" && __g.ck || "001_<134字符常量>") + '-' + meta.content;
  document.cookie = e+"=; expires=Mon, 20 Sep 1970 ...; path=/;";            // 清旧
  document.cookie = e+"="+t+"; domain=.zhihu.com; expires=+630e8ms; path=/;"
                    + (https ? " SameSite=None; Secure" : "");              // 写新（≈729 天）
  sessionStorage.setItem("zap:zse_ck_referrer", document.referrer);
  window.location.reload();                                                 // 重载 → 浏览器带新 cookie 重发原请求
}
```

**「报告」的形式就是 reload 后浏览器自动携带的 cookie 本身**，没有任何额外 XHR。

---

## ② 服务端是否需要遥测报告才信任 cookie？

**不需要。三重独立证据：**

1. **zhihu-proxy（HGXY2004）= 最干净的对照实验**：它的 node:vm 沙箱**根本没给 XMLHttpRequest 绑定**（源码 grep 零命中）——tracker 一旦尝试发送，`new XMLHttpRequest` 直接 ReferenceError，被 tracker 自己的 `e()` 包装器静默吞掉。即该实现**物理上不可能发出任何遥测**，但其 README 实测记录 `__zse_ck` 正确求解、API 通道工作（机房 IP 仅 HTML 页面被声誉层 40362——与挑战无关）。
2. **我方求解器**：`XMLHttpRequestStub.send()` 是 noop、fetch 直接 reject——产出真 `005_` PoW 值，**挑战页不再返回**（挑战关已过）。
3. **RSSHub PR#22319**（上游合并、生产在用）：JSDOM `runScripts:'outside-only'` 跑官方 JS 收 cookie，全程无任何「先上报再采信」的步骤；合并后社区大规模使用。

**反证 40362 与遥测无关**（本项目 `03_EdgeOne挑战.md` 已归档的 fxzhihu 对照实验）：
- PR#64：同主机同签名同 cookie，仅 UA 换成缩写 `Mozilla/5.0` → 200 变 40362；换 cookie 无效。**40362 是 UA 完整性维度的硬拒**。
- PR#65：签名不覆盖 query → 40362。
- 实测：只带 d_c0+z_c0+正确签名+完整 UA 可 200——该场景连 `__zse_ck` 都不是必要条件，遑论遥测。

**结论：遥测缺失至多是风控评分的一个弱特征，不是信任门槛。40362 排障应按既有排障树：① UA 完整性 → ② cookie 三元组同会话 → ③ 签名覆盖 path+query → ④ IP 声誉 → ⑤ 重跑挑战。**

### 服务端靠什么建立「解题档案」（综合模型）

| 环节 | 机制 | 证据 |
|------|------|------|
| 签发 | 每挑战下发 **128 字符种子**（meta content）+ **当次现编的个性化挑战 JS**：8 份缓存 WASM md5 两两不同、长度 335446–335455 抖动、JS 全文 95% 字节不同（全量重混淆）——URL 即内容 sha256，SRI 自校验 | 8 份缓存 diff；活捉页 |
| 绑定 | JS/WASM 谜面与种子**同次签发配对**；种子在 JS 里仅被 `sc()` 读一次作 cookie 后缀（全文 `zh-zse-ck`/`getElementById` 各仅 1 次，均在 sc 内）——PoW 输入内嵌于当次 WASM | 字符串计数 |
| 提交 | reload 后请求自动带 `__zse_ck = <005_ck>-<种子>`；cookie domain=.zhihu.com，TTL 630e8 ms≈729 天 | sc() 原文 |
| 校验 | 服务器按种子找回/重算当次谜面答案验证 ck；`005_`=真 PoW 通过标记，`001_`=**硬编码全同的失败回退值**（8 份文件逐字节相同，一眼识别未求解） | 8 份 fallback 比对 |
| 旁证 | 签发/回执的 IP、UA、d_c0 一致性；EdgeOne 边缘层 + 知乎应用层（40362/40353）双关 | 03/05 期调研 |

---

## ③ 除 XHR 外有无 sendBeacon / Image ping？

**没有。** 8 份 JS + 内嵌 WASM 逐字节核查：

| 通道 | JS 层 | WASM 层 |
|------|-------|---------|
| `navigator.sendBeacon` | 0 | 0 |
| `new Image()` / `.src=` 打点 | 0（求解器桩从未被触发亦可旁证） | 0 |
| `fetch` | 0 | 0 |
| WebSocket / EventSource | 0 | 0 |
| 动态插 `<script>`/`<img>` 上报 | 无（createElement 仅出现在 DOM 桩交互，无 URL 赋值） | — |

挑战页 HTML 全文也只有**一个** script 标签（挑战 JS 自身），无其他采集器脚本（活捉页核实）。

---

## 附：对我方当前 40362 问题的定位建议

遥测假设可排除。按概率排序复核：① 请求头 UA 与 Sec-Ch-Ua 的完整性/一致性（40362 最敏感维度）；② x-zse-96 是否用**所发送的同一个 d_c0** 签、且 apiPath **含 query**（当前 verify 用 `2.0_placeholder` 占位=必拒，属预期）；③ d_c0 与 __zse_ck 同会话签发（预热 /explore → 触发挑战 → 求解必须同一 d_c0 链）；④ 本机/机房 IP 声誉（对照：换住宅代理同一脚本复测）。

## 证据文件

- 挑战 JS 缓存：`/tmp/zse_ck_js_*.js`（8 份）｜ 活捉挑战页：`/tmp/challenge_live.html`
- WASM 提取：`/tmp/zse_wasm/wasm_0.bin`（335 KB，TinyGo 0.31.2）
- 开源对照：RSSHub PR#22319 diff（`/tmp/rsshub_22319.diff`，JSDOM 沙箱段已引）；`research/zhihu-proxy-src/zhihu-proxy/lib/zseck.mjs`（无 XHR 绑定）+ 其 README 能力边界节；`research/03_EdgeOne挑战.md`（fxzhihu PR#64/#65 对照实验归档）
