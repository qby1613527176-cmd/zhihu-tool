# 12a · HGXY2004/zhihu-proxy 源码精读：__zse_ck 沙箱求解与「服务端解题档案」问题

> 调研日期：2026-11-06 · 调研人：T1 子代理
> 源码获取：`git clone --depth 1 https://github.com/HGXY2004/zhihu-proxy.git`（HEAD=7abd9f8，单提交）+ `HGXY2004/astrbot_plugin_zhihu_parse.git`（HEAD=cb0b8e6）
> 本地副本：`zhihu_tool/research/zhihu-proxy-src/`
> 仓库规模：8 个业务文件 ~1300 行（零第三方依赖，Node≥18），vendor 官方 JS 约 10 万行。GitHub issues：**0 条**（API 实测空列表）。

---

## 结论速览（四问四答）

| # | 问题 | 答案 |
|---|------|------|
| ① | 挑战 JS 的 XHR/fetch 怎么处理？ | **吞掉/桩掉，不是真发**。`fetch` 被替换为 `Promise.reject('sandbox fetch blocked')`；`XMLHttpRequest`/`sendBeacon` **完全没有 shim**——挑战脚本若 `new XMLHttpRequest()` 直接 ReferenceError，遥测全部静默丢失 |
| ② | 产出 cookie 有真实 API 成功实证吗？ | **没有用于 HTML 通道的成功证据，反而有失败实证**。README 自报：API 通道（d_c0+x-zse-96 签名，**不依赖 __zse_ck**）文章接口 200 ✅；HTML 通道（依赖 __zse_ck）在机房 IP 下 **40362 ⚠️「__zse_ck 已正确求解但被声誉层拦截」**。astrbot 生产插件实证「机房 IP → T2 反代成功解析文章」，走的也是 API 签名通道 |
| ③ | 沙箱比我们多了什么 shim？ | 清单见 §4。关键差异项：它有 `PerformanceObserver`/`MutationObserver`/`IntersectionObserver`/`matchMedia`/`getComputedStyle`/`Image` 桩、document.cookie 捕获器、location.reload 捕获器；**与我们一样没有 XHR/sendBeacon/WebSocket/canvas/Worker**。签名沙箱额外有 `crypto.getRandomValues/subtle`（zseck 沙箱连 crypto 都没有） |
| ④ | 有「验证/激活」步骤吗？ | **没有**。解出 `__zse_ck` 后直接 merge 进 cookie jar 重试原 URL，全流程无任何主动遥测/激活请求 |

**对我方假设的意义**：zhihu-proxy 是「同策略（吞遥测）→ 同症状（40362）」的独立第三方交叉验证。它解出的是真 PoW（005_ 前缀），HTML 通道照样被 40362 拒——与我们沙箱完全一致。它的作者把 40362 归因于「机房 IP 声誉层」，但我们「同 IP 同 d_c0、旧浏览器 cookie=200 vs 新解 cookie=40362」的对照实验排除了纯 IP 归因 → **差异锁定在签发上下文（遥测档案/环境指纹），而非 IP**。另外在其 vendor 官方签名包里发现了知乎内置遥测端点（`sc-critical`、埋点 SDK sendBeacon），佐证「知乎官方 JS 运行时必然向服务端报备」这一机制真实存在（详见 §5）。

---

## 1. 项目定位与整体架构

单作者（HGXY2004）2025 年作品，定位「本机反向代理 & 文章提取器」，四层反爬（README 自述）：

1. **会话预热**：匿名 `GET www.zhihu.com/explore` 拿 `d_c0`/`_zap`/`BEC`，持久化 `data/session.json`
2. **x-zse-96 签名**：不做算法重写，把知乎官方 webpack 模块包（`vendor/other.js` 99k 行 + `vendor/zhihu-runtime.js` 641 行，提取自 cv-cat/ZhihuApis）放进 `node:vm` 沙箱，直接调知乎自己的 `tv()` 函数
3. **__zse_ck 挑战求解**：`lib/zseck.mjs` 在 vm 沙箱补浏览器环境，直接运行官方挑战脚本，秒级求解真 PoW（`005_` 前缀，非 `001_` 回退值）
4. **风控规避**：全局请求串行化 + 650ms 最小间隔 + 0-250ms 抖动；40362 自动弃匿名会话重预热退避重试

文件结构：

```
server.mjs          # HTTP 反代（node:http 零依赖，端口 8964）
cli.mjs             # 命令行提取
lib/sign.mjs        # x-zse-96 签名（vm 沙箱跑官方 JS，tv() 导出）
lib/zseck.mjs       # __zse_ck 求解 + CookieJar + fetchWithChallenge
lib/session.mjs     # 会话：预热/持久化/登录cookie/限速/签名请求
lib/extract.mjs     # URL 解析 + 文章/回答/问题/想法提取
lib/markdown.mjs    # HTML→Markdown
lib/proxycore.mjs   # 透明转发 + URL 重写
vendor/other.js + zhihu-runtime.js  # 知乎官方签名 webpack 包
```

---

## 2. 问题①：挑战 JS 的 XHR/fetch 被怎么处理

### 2.1 fetch —— 显式桩掉拒绝

`lib/zseck.mjs:134`（`makeSandbox` 内）：

```js
const sandbox = {
    document, location, navigator, screen, performance, PerformanceObserver,
    sessionStorage: { ... }, localStorage: { ... },
    atob: ..., btoa: ...,
    setTimeout: (fn, ms, ...a) => setTimeout(fn, Math.min(ms || 0, 2000), ...a),
    ...
    fetch: () => Promise.reject(new Error('sandbox fetch blocked')),   // ← 关键行
    console: { log: () => {}, ... },
    __captured: captured,
};
```

挑战脚本内任何 `fetch()` 调用 → 立即 rejected promise，**不会发出任何真实网络请求**。

### 2.2 XMLHttpRequest / sendBeacon / WebSocket —— 完全没有 shim

全仓 grep 实证：

```
$ grep -rn "XMLHttpRequest|sendBeacon|WebSocket|EventSource" lib/ server.mjs cli.mjs tests/
（0 匹配 —— 业务代码完全没有提供这些 API）
```

挑战脚本若执行 `new XMLHttpRequest()` → `ReferenceError: XMLHttpRequest is not defined`。其后果取决于挑战脚本自身的容错：

- 若遥测代码有 try/catch 保护 → 静默跳过，不影响 cookie 写入（zhihu-proxy 能解出 cookie 证明主流程如此）
- 求解循环也不依赖任何网络回调——`solveZseCk` 只是轮询 `document.cookie` setter 捕获数组和 `sandbox.__g.ck`（见 §3.2）

### 2.3 对照：真实浏览器里这些调用会发生什么

zhihu-proxy 的 vendor 签名包（知乎官方 webpack）里就内置了同类遥测（详见 §5）：XHR POST `https://www.zhihu.com/sc-critical`、`navigator.sendBeacon(reportUrl, events)`。在真实浏览器中，挑战脚本运行全程的网络请求（解题开始/进度/结果/指纹）会携带 cookie 发到服务端——这正是我们假设的「解题档案」。**zhihu-proxy 与我们的沙箱一样，把这条链路完全掐断了。**

---

## 3. 问题②：产出 cookie 的真实使用实证

### 3.1 README「能力边界」表（作者数据中心 IP 实测自述）

| 资源 | 匿名（仅 d_c0+签名） | 备注 |
|---|---|---|
| 文章 `zhuanlan.zhihu.com/p/{id}` | ✅ API 200 | 走 `www.zhihu.com/api/v4/articles/{id}` 签名通道 |
| 回答、问题、热榜 | ❌ 40353 need_login | 注入 z_c0 后走同一签名通道 ✅ |
| **HTML 页面** | **⚠️ 40362** | **「`__zse_ck` 已正确求解，但机房 IP 被声誉层拦截」**；`/explore`、`/signin` 等少数页面可直接 200；住宅 IP/登录态下 HTML 通道可用 |

关键解读：

- ✅ 的成功**全部来自 API 签名通道**（`x-zse-96` + `d_c0`），`__zse_ck` 不参与 API 鉴权
- `__zse_ck` 唯一的使用场景是 HTML 页面通道，而作者自己实测：**解对了也被 40362 拒**——和我们一模一样
- 作者的归因是「机房 IP 声誉层」，但他**没有做过同 IP 浏览器 vs 沙箱的对照实验**（我们的对照更强：同 IP 同 d_c0，唯一变量是 __zse_ck 签发上下文 → 锁定签发上下文差异）

### 3.2 生产插件 astrbot_plugin_zhihu_parse 的实证

同作者的 AstrBot 生产插件，降级链设计（`core/degrade.py`、README）：

```
T1 direct/desktop|ios|mobile   curl_cffi TLS 指纹伪装直连 HTML
T2 proxy/extract               本机 zhihu-proxy 反代 /extract（= API 签名通道）
T3 proxy/fetch-html            经反代取原始 HTML（= __zse_ck 挑战通道）
```

README 实测记录：

> 实测（阿里云数据中心 IP）：T1 三层全部命中 `__zse_ck` 挑战 → **T2 成功解析文章**；住宅 IP 下通常 T1 第一层即成功

注意：**T2 成功走的是 API 签名通道**（`/extract` 内部对文章调 `signedApiGet('https://www.zhihu.com/api/v4/articles/{id}')`），不是挑战通道。这再次印证：成功路径绕开了 `__zse_ck`，挑战通道在机房 IP 下无人宣称成功。

### 3.3 失败处理（session.mjs 摘录）

40362 的实际应对是「弃会话重预热」，而非任何挑战补救：

```js
// 风控码（40362 请求异常限制 / 10003 参数异常 / 泛 403）→ 换新匿名会话 + 退避重试
for (let i = 0; i < 2 && retryable(r) && this.stats.rewarmups < 6; i++) {
  this.stats.rewarmups++;
  this.anon = new CookieJar();                       // ← 整个匿名 jar 丢弃
  this.logger(`[session] risk control (code=${...}) → re-warmup + retry ${i+1}/2`);
  await this.warmup({ force: true });                // ← 重新 GET /explore 拿新 d_c0
  await new Promise((z) => setTimeout(z, 1200 * (i + 1) + Math.random() * 800));
  r = await attempt();
}
```

`proxycore.mjs` 的 API 转发同样：`res.status === 403 && body.includes('40362')` → 弃会话重预热重试一次。

---

## 4. 问题③：沙箱环境清单

### 4.1 zseck 挑战沙箱（`lib/zseck.mjs` `makeSandbox`，全文 130 行）

| 类别 | 提供项 | 实现要点 |
|---|---|---|
| document | `readyState='complete'`、`referrer`、`title`、`characterSet`、`hidden=false`、`visibilityState='visible'`、`currentScript`(=tracker 脚本元素)、`head`/`body`/`documentElement`（含 style、clientWidth/Height=1920/1080）、`getElementById`（只认 `zh-zse-ck`）、`getElementsByTagName`（meta/script 各返一个桩元素）、`querySelector`（按字符串含 `zse-ck`/`assets-tracker-config` 分发）、`querySelectorAll=[]`、`createElement`（返回通用桩）、`createTextNode`、事件监听注册表 | **`cookie` 是 defineProperty getter/setter**：setter 把每次写入 push 进 `captured.cookies[]` 并维护单行 `cookieStore` —— 这是取回 `__zse_ck` 的主通道 |
| location | href/protocol/host/hostname/origin/pathname/search/hash 固定为 `zhuanlan.zhihu.com` 系列 | `reload`/`replace`/`assign` 只置 `captured.reloaded=true`，**不真跳转** |
| navigator | UA(=sign.mjs 统一常量 Chrome126/Win)、appVersion、platform=Win32、language/languages、vendor='Google Inc.'、maxTouchPoints=0、hardwareConcurrency=8、deviceMemory=8、cookieEnabled=true、**webdriver=false**、plugins.length=0、connection.effectiveType='4g' | 无 sendBeacon |
| screen | 1920×1080 / availHeight 1040 / colorDepth=pixelDepth=24 | |
| performance | `now()`（hrtime 微秒）、timeOrigin、timing、getEntriesByType/Name=[]、mark/measure | + `PerformanceObserver` 空桩类 |
| 存储 | sessionStorage/localStorage 空桩 | getItem 永远 null |
| 定时器 | setTimeout **clamp 到 ≤2000ms**、setInterval clamp 到 ≥50/≤200ms、rAF→16ms setTimeout | 防 PoW 死循环拖死宿主 |
| 编码 | atob/btoa（Buffer 实现） | |
| 网络 | **`fetch`=reject 桩**；**无 XMLHttpRequest、无 sendBeacon、无 WebSocket、无 EventSource** | ← 遥测全灭 |
| 其他 | `Image`/`MutationObserver`/`IntersectionObserver` 空桩、`getComputedStyle`（返空 getPropertyValue）、`matchMedia`（matches=false）、console 静默、`window=self=globalThis=top=parent=frames` 全自指 | |
| **没有** | **crypto（getRandomValues/subtle 全无）、Worker、canvas/2d context、WebGL、AudioContext、Notification、history、indexedDB、FontFace** | |

### 4.2 sign 签名沙箱（`lib/sign.mjs` `createSigner`）

比 zseck 沙箱少 `PerformanceObserver`/rAF 捕获等，但**多了**：

- `URL, URLSearchParams, TextEncoder, TextDecoder`（直接透传 Node 原生类）
- **`crypto = { getRandomValues (Math.random 实现!), subtle: {}, randomUUID: 固定格式串 }`** —— 签名随机因子来源，⚠️ 用的是 `Math.random` 而非密码学随机（两次签名不同但格式一致，服务端均接受，smoke test 证明）
- `history`、`alert/confirm/prompt` 桩

加载与调用：

```js
vm.runInContext(otherSrc, sandbox, { timeout: 30000, filename: 'other.js' });
vm.runInContext(runtimeSrc, sandbox, { timeout: 30000, filename: 'zhihu-runtime.js' });
if (typeof sandbox.tv !== 'function') throw new Error('tv() 未在沙箱中找到');
// 签名调用：
const res = sandbox.tv(url, body || '', { zse93: '101_3_3.0', dc0: dC0, xZst81: null }, '');
return { zse93: '101_3_3.0', zse96: '2.0_' + res.signature, source: res.source };
```

加载 ~100ms（进程级单例 `getSigner()`），单次签名 <1ms。

### 4.3 vendor/zhihu-runtime.js 的反检测补丁（值得注意）

开头 17 行是对官方 JS 环境检测的针对性伪装（cv-cat/ZhihuApis jsdom 方案的遗产）：

```js
Object.prototype.toString = function () {
    if(this.constructor.name === 'Document') return '[object HTMLDocument]'
    else if(this.constructor.name === 'CanvasRenderingContext2D') return '[object CanvasRenderingContext2D]'
    return xxx.call(this, arguments)
}
window._resourceLoader = undefined       // jsdom 内部属性清理
window._sessionHistory = undefined
Function.prototype.toString = function () {
    if(this.name === 'Window') return 'function Window() { [native code] }'
    return aaa.call(this, arguments)
}
```

说明知乎官方包会：① 用 `Object.prototype.toString.call(document)` 验 HTMLDocument 身份；② 用 `Function.prototype.toString` 验 Window 是否原生。**这是「官方 JS 主动检测执行环境」的直接代码证据**——它检测的不只是值，还有对象的身份/原生性。

### 4.4 与我们沙箱的对比要点

（基于任务背景描述的我方实现）

| 维度 | zhihu-proxy | 我方（现状） |
|---|---|---|
| fetch/XHR 遥测 | 全吞（fetch reject、无 XHR） | 同样吞掉 |
| 产出 PoW | 真值（005_ 前缀） | 真值（005_ 前缀）且过挑战页 |
| HTML/API 结果 | HTML 40362（机房 IP）；API 靠签名通道 200 | API 40362（同 d_c0 对照实锤是 cookie 签发上下文差异） |
| crypto | 签名沙箱 Math.random 版；zseck 沙箱无 | 待核对我们 zseck 沙箱是否有真 crypto |
| 环境检测对策 | runtime 补丁伪造 toString | 待核对 |

---

## 5. 重大附带发现：知乎官方 JS 内置遥测端点（vendor/other.js）

`vendor/other.js` 是知乎**官方签名 webpack 包**（不是挑战脚本），其中存在两处向服务端报备的通道：

### 5.1 `sc-critical` 安全事件上报（模块 62082，other.js:10360-10370）

```js
eb = function(er) {
    var eo = new XMLHttpRequest
      , ei = new URL("https://www.zhihu.com/sc-critical");
    ei.searchParams.append("report_to_statsd", "1"),
    eo.open("POST", ei.toString()),
    eo.setRequestHeader("X-Requested-With", "XMLHttpRequest"),
    eo.withCredentials = !0;                       // ← 携带 cookie
    var ec = null == window ? void 0 : window.btoa(encodeURIComponent(JSON.stringify(er)));
    eo.send(ec)
};
// 同模块导出 eA：window.Raven.captureException(...)（Sentry 错误监控）
```

- 端点 `https://www.zhihu.com/sc-critical?report_to_statsd=1`：「sc」高度疑似 **security/safe-guard critical** 缩写，payload 是 base64(JSON) 结构化事件，`withCredentials=true` 携带会话 cookie —— **服务端可以把遥测事件与 cookie 身份直接关联**。这与我们假设的「解题档案」机制同构。
- 在 zhihu-proxy 的 sign 沙箱里，该模块一旦被调用即 `ReferenceError: XMLHttpRequest is not defined`（除非调用方 try/catch）→ 上报丢失。然而 API 依然 200 —— 说明**匿名文章 API 对签名包遥测缺失不敏感**（至少低敏资源如此）。

### 5.2 行为埋点 SDK sendBeacon（other.js:42190-42244）

```js
ej.prototype.send = function(er, eo) {
    ...
    er.session_id = eo.collect.sessionManager.getSessionId(),
    er.params = JSON.stringify(er.params),
    ...
    ec = ei.reportUrl,
    eo && window.navigator && window.navigator.sendBeacon
        ? window.navigator.sendBeacon(ec, JSON.stringify(eu)) || ep(ec, eu)
        : eR(ec, eu, ef, 0, ed, ep, 0, "", !1)     // ← 降级 XHR POST (other.js:41048)
    ...
};
// 异常兜底：catch (er) { console.warn("something error, " + ...) }
```

标准用户行为埋点：带 `session_id`、`evtParams`、user/header 信息，`sendBeacon` 优先、XHR 降级，异常被静默吞掉。zhihu-proxy 沙箱 navigator 无 `sendBeacon`、无 XHR → 走 `eR` 时 ReferenceError → 被外层 try/catch 吞 → 埋点全丢。

### 5.3 对挑战脚本的推论

zse-ck v4 挑战脚本（运行时从 `static.zhihu.com/zse-ck/v4/*.js` 下载，zhihu-proxy 不落盘）与签名包同属知乎前端安全体系。**由签名包内置遥测推断，挑战脚本在解题过程中/完成后向服务端报备（建解题档案）是大概率事件**——这正是我们 40362 对照实验指向的机制。验证它需要抓到真实挑战 JS 做静态分析（本调研环境 DNS 无法解析 zhihu.com，未能实测下载，建议在可访问环境抓取：`curl 任意会触发挑战的页面 → 提取 <script src=...zse-ck...> → 下载分析其 XHR/fetch/beacon 调用点`）。

---

## 6. 问题④：是否有「验证/激活」步骤

**没有。** `fetchWithChallenge`（zseck.mjs:192-241）全流程：

```
GET 目标 URL（带 jar 现有 cookie）
  ↓ 403 且 content-type 含 html
解析挑战页：<meta id="zh-zse-ck" content="..."> + <script src="...zse-ck...js">
  ↓（脚本源码有 Map 缓存，同 URL 不重复下载）
GET static.zhihu.com 下载官方挑战 JS
  ↓
solveZseCk：vm 沙箱运行官方脚本，≤12s 轮询两条取回通道：
    a) captured.cookies 里找 `__zse_ck=` 前缀（document.cookie setter 捕获）
    b) 兜底 sandbox.__g.ck + '-' + metaContent
  ↓ 拿到 cookie
jar.merge('__zse_ck=' + solved.cookie)
  ↓
重试原 URL（maxAttempts=3 循环）
```

- 唯一主动发出的网络请求：①目标页面 ②挑战脚本下载（static 域，不带 jar cookie）——**没有任何指向遥测/激活端点的请求**
- `solveZseCk` 运行期 `vm.runInContext` 有 15s timeout，取回 cookie 轮询 12s 上限
- 定时器 clamp（setTimeout≤2000ms、setInterval 50-200ms）防 PoW 把宿主拖死
- 解出的 `__zse_ck` 会回写匿名 jar 并持久化 `data/session.json`，后续 HTML 请求直接复用（`session.htmlGet` 中 `before/after` 对比计数 `challengesSolved`）

---

## 7. 其他值得借鉴的工程细节

1. **全局串行限速**（session.mjs）：promise 链队列，`minIntervalMs=650` + 0-250ms 随机抖动，`lastRequestAt` 单调。proxycore 注释：「curl 等客户端 UA 会被知乎风控直接 40362」→ 代理层强制统一浏览器 UA + Sec-Ch-Ua 三件套。
2. **挑战脚本缓存**：`scriptCache = Map(url → jsSrc)`，同版本挑战 JS 只下载一次。
3. **CookieJar 极简**（zseck.mjs）：Map 结构，absorb Set-Cookie / merge cookie 字符串 / 输出 header。登录层与匿名层分离（`session.login` vs `session.anon`），合并时登录优先、请求级 `x-zhihu-cookie` 透传最高优先；**签名 d_c0 必须与实发 cookie 一致**（`dC0With(override)` 先从透传里找 d_c0）。
4. **API 域选择**：`api.zhihu.com` 对机房 IP 风控更严 → 改用 `www.zhihu.com/api/v4/...` 同路径签名调用（proxycore HOST_ALIAS 注释）。
5. **回答/问题解锁边界**：匿名只能拿文章 API；回答/问题/热榜匿名 40353 need_login，必须注入 z_c0——与签名通道正交。
6. **透明反代**：`/zhihu/<alias>/<path>` 转发后把响应体内 `*.zhihu.com`/`*.zhimg.com` URL 全部重写为本地代理路径（含 JSON 转义形态），浏览器可在反代内持续浏览——但我们目标场景用不到。

---

## 8. 对我方下一步的建议（由证据直接推出）

1. **首要验证实验**：在可访问知乎的环境抓真实 zse-ck v4 挑战 JS，静态分析其网络调用点（fetch/XHR/beacon 的 URL、时机、payload 结构）。预期能找到类似 `sc-critical` 的报备端点。若证实 → 我方沙箱把遥测 XHR **真发出去**（带 cookie、withCredentials、正确 Content-Type）后再用新解 cookie 调 API，观察 40362 是否消失。
2. **沙箱 shim 补强清单**（对照 §4.1 我们没有而它有的，以及它也没有但官方 JS 可能探的）：`PerformanceObserver`、cookie setter 捕获器、reload 捕获器、**crypto.getRandomValues（密码学随机）**、canvas/2d context 指纹面、Worker。
3. **环境检测对抗**：参考 vendor/zhihu-runtime.js 的 `Object.prototype.toString`/`Function.prototype.toString` 补丁思路——官方 JS 会验证对象身份与函数原生性，建议审计我方沙箱同类检测面。
4. **保底路线**：若目标只是内容获取而非攻破 HTML 挑战，zhihu-proxy 证明了「d_c0 预热 + x-zse-96 官方 tv() 签名 + 串行限速 + 40362 弃会话重试」在机房 IP 匿名场景可稳定拿文章 API——这条路线完全绕开 __zse_ck，可直接复用其 vendor 文件与调用方式（`tv(url, body, {zse93:'101_3_3.0', dc0, xZst81:null}, '')` → `'2.0_'+signature`）。
5. **40362 应对纪律**：它实测有效的策略是「弃匿名 jar → 重新 warmup 新 d_c0 → 1.2s×n+抖动退避 → 最多重试 2 次」，且 rewarmup 总预算 <6 防死循环。

---

## 附：本调研的局限

- 本机 DNS 无法解析 zhihu.com（curl exit 6），未能实测下载 zse-ck v4 挑战 JS 验证其遥测调用点——§5.3 仍为强推论而非实锤，需按 §8.1 补验。
- GitHub API 实测该仓库 issues 为空，无社区佐证/争议记录可参考。
- 仓库为单提交（2025 年），无历史演进可考；作者对 40362 的「IP 声誉」归因未经对照实验，证据强度低于我方实验。
