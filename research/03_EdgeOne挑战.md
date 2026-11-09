# 03 知乎 zse_ck 挑战与腾讯 EdgeOne Bot 管理 — 绕过方案调研

> 调研时间：2026-11-06 · 方法：GitHub API（issues/PR/diff）+ patch-diff 直取（tavily/web_search 不可用，DDG/Bing 被网络阻断）
> 核心信源：RSSHub PR#22319（已合并）、PR#22317、Issue#20402；fxzhihu PR#64/#65；myagenttool PR#1680

## 结论摘要

1. **zse_ck 是知乎自研挑战层，不是 EdgeOne 原生功能**。挑战 JS 托管在知乎自有域 `static.zhihu.com/zse-ck/v4/<hash>.js`，种子在挑战页 `<meta id="zh-zse-ck">`（`zh`=知乎）。EdgeOne（`x-secng-*`/`eo-log-uuid`）只是前面的 CDN/边缘 WAF，两者是**前后两层**：EO 做边缘过滤，zse_ck 做应用层挑战。
2. **zse_ck v4 = WASM VM 计算型挑战 + 轻量环境探测**，已被开源界彻底打穿：**RSSHub PR#22319（2026-07-13 合并）在纯 Node.js 里用 JSDOM + node:vm 直接执行知乎官方挑战 JS 产出 `__zse_ck`，无需任何浏览器**，3 秒内出 token。所需环境 shim 极少（navigator.UA / webdriver=false / TextEncoder / atob-btoa / `__g` / document.cookie setter 钩子）。
3. **`__zse_ck` 与生成时的 User-Agent 绑定**，并与 `d_c0` 后端交叉校验，数天轮换 → 三元组（UA + d_c0 + __zse_ck）必须同会话一致，且要缓存复用（RSSHub 缓存 30min）。
4. **40362 是知乎应用层风控码，UA 完整性是独立触发条件**：fxzhihu 对照实验（PR#64）——同主机同签名同 cookie，仅把 UA 换成缩写版 `Mozilla/5.0` 就从 200 变 403/40362；换 cookie（d_c0/z_c0）无效。签名还必须覆盖 path+query（PR#65）。
5. **没有发现针对 EdgeOne(x-secng) 的通用开源绕过项目**；最接近的社区实证（myagenttool PR#1680，2026-08 合并）：所有「新鲜」自动化浏览器（chromium headless/headed + webdriver patch + warmup、firefox、webkit）一律 403 → 强制跳 /signin 并被打上 `gdxidpyhxdE` 反自动化 cookie，只有**登录过的持久 Chrome profile**（`launchPersistentContext`）能过。与我们的结论（伪装 UA + stealth 真实浏览器可自动过）方向一致，且互补：他们没解决「新鲜浏览器」问题，我们解决了。
6. 我们的「真实浏览器自动过挑战」方案与 RSSHub 未合并但 e2e 验证过的 PR#22317 同构（冷启动 ~18s），有效；**补强方向是加一条 JSDOM 纯 Node 快速通道做日常续期，浏览器方案做兜底**。

## zse_ck 技术原理

### 挑战流程（v4，当前线上版本；RSSHub PR#22319 实测逆向）

```
客户端请求 zhihu 页面(带 d_c0，__zse_ck 缺失/占位如 005_x-x)
  → 返回挑战 HTML：
      <meta id="zh-zse-ck" content="<种子>">          ← 每次挑战的新鲜种子
      <script src="https://static.zhihu.com/zse-ck/v4/<hash>.js">  ← 带版本 hash 的挑战 JS
  → 挑战 JS（WASM VM 混淆）读取 meta 种子 + 环境信息，计算 token
  → 写入 document.cookie: __zse_ck=<token>           ← 形如 xxx-xxx 含连字符
  → 后续请求带 __zse_ck + d_c0 + 一致的 UA → 放行
```

### 关键性质（全部有开源实证）

| 性质 | 证据 |
|:--|:--|
| **计算型为主**：WASM VM 对 meta 种子做运算产 token；报错特征 `RuntimeError: unreachable`（典型 WASM trap） | RSSHub PR#22319 代码注释「WASM VM did not produce a __zse_ck」+ 专门 guard 吞掉 WASM `unreachable` 异常 |
| **轻量环境探测**：读取 `navigator.userAgent`（**token 与生成时 UA 绑定**，后续请求 UA 不一致即失效）、探测 `navigator.webdriver`、需要 `TextEncoder/TextDecoder`、`atob/btoa`、全局 `__g` 对象 | PR#22319 注释「__zse_ck is checked against the user-agent that generated it」及 shim 清单 |
| **不需要真渲染**：JSDOM（无 canvas/WebGL/布局引擎）即可跑通 → 挑战不依赖重指纹（canvas/WebGL/audio 不在硬校验路径，或仅有容错） | PR#22319 用 JSDOM + `runScripts:'outside-only'` + node:vm 成功产 token，已合并进 RSSHub master |
| **与 d_c0 交叉校验**：后端验 __zse_ck↔d_c0 一致性；数天轮换 | RSSHub PR#22317 描述：「generated at runtime by Zhihu's JS from the device fingerprint and d_c0, rotates every few days, cross-checked against d_c0」 |
| **v3→v4 演进**：旧版可从 `static.zhihu.com/zse-ck/v3.js` 正则提一个站点级静态值（`__g.ck\|\|"..."`）直接用——该捷径**已失效**，v4 改为按挑战动态计算 | RSSHub 旧代码（PR#22319 删除部分） |
| **配套签名层**：`x-zse-96 = "2.0_" + g_encrypt(md5("101_3_3.0" + "+" + apiPath + "+" + d_c0))`（v3 算法开源多年，未变）；签名必须用**所发送 cookie 里同一个 d_c0**，且 apiPath 要含 query | RSSHub `execlib/x-zse-96-v3`；fxzhihu PR#65（签名不含 query → 40362）；算法出处 srx-2000/spider_collection#18 |
| **登录墙加码**：多数内容（专栏列表/回答/动态）现在还要 `z_c0` 登录 cookie，否则 403 | RSSHub PR#22317 说明 + Issue#20402 |

## EdgeOne 检测维度

知乎响应头 `x-secng-*`（SecNG = 腾讯安全新一代边缘引擎）+ `eo-log-uuid`（EdgeOne 请求日志 ID）表明知乎整站套在**腾讯 EdgeOne**（边缘安全加速平台）后面。检测分两层：

### 边缘层（EdgeOne / SecNG）— 部分为实证、部分为产品常识推断（已标注）

| 维度 | 证据/性质 |
|:--|:--|
| **JS 挑战分发**：对无可信 cookie 的客户端返回挑战页（裸 HTTP → 403 + zse_ck 挑战页） | ✅ 我们实测 |
| **反自动化硬拒 + 打标**：新鲜自动化浏览器（chromium/firefox/webkit，含 webdriver patch、warmup 套路）全 403 → 强制 `/signin`，并种下 `gdxidpyhxdE` 反自动化 cookie | ✅ myagenttool PR#1680 实测 |
| **IP 信誉/机房 IP 惩罚**：RSSHub 用户实证——家宽 IP 换新鲜 cookie 即恢复；VPS 机房 IP 全套 403，换 cookie 无效 | ✅ RSSHub Issue#20402 评论区多人实证 |
| **TLS/HTTP2 指纹（JA3 等）** | ⚠️ 推断（EO Bot 管理产品标配；无直接开源实证，列入待验证） |
| **行为/频率分析、会话管理、人机验证（验证码）升级档** | ⚠️ 推断（EO Bot 管理产品标配功能项） |
| **请求头完整性/一致性**：`X-SecNG-Response` 头存在于爬虫穿透时的响应里（Norconex committer-sql#17 的字段映射事故里露出），说明 EO 在响应路径做标注 | ✅ 间接佐证 |

### 应用层（知乎自身）— 全部实证

| 维度 | 触发后果 |
|:--|:--|
| **UA 完整性**：缩写/ bot UA（`Mozilla/5.0`、`node`、无头默认 UA）→ **40362 硬拒**（不给挑战机会） | fxzhihu PR#64 对照实验；我们的实测一致 |
| **cookie 三元组一致性**：d_c0 / __zse_ck / z_c0 须同一会话导出，缺一/错配 → 403 或 40362 | RSSHub Issue#20402（「三项都最新且同账号」才恢复） |
| **签名正确性**：x-zse-96 须用所发 d_c0 签、覆盖 path+query → 否则 40362 | fxzhihu PR#65 |
| **__zse_ck↔UA 绑定**：换 UA 不换 __zse_ck（或反之）→ 拒绝 | RSSHub PR#22319 注释 |
| **登录态**：z_c0 缺失 → 大部分内容 403 | RSSHub 官方说明 |

## 已知绕过技巧（开源界，按有效性排序）

### 1. JSDOM 无浏览器跑挑战（最优，已合并进 RSSHub master）
RSSHub PR#22319（2026-07-13 合并，`lib/routes/zhihu/utils.ts`）：
- 种子 d_c0：GET `https://www.zhihu.com/explore`（`redirect:manual`）取 Set-Cookie；
- 触发挑战：带 `d_c0=...; __zse_ck=005_x-x` 请求目标 API 路径（`x-requested-with: fetch`），从 403 挑战页抠 `meta#zh-zse-ck` 种子和 `zse-ck/v4/<hash>.js` URL；
- JSDOM 构造最小 DOM（meta 种子 + `<script data-assets-tracker-config='{"appName":"zse_ck"}'>`），`runScripts:'outside-only'`，shim：`navigator.userAgent`（**用 header-generator 随机一个真实 UA 并全程固定**）、`webdriver=false`、`TextEncoder/TextDecoder`、`atob/btoa`、`__g={}`；hook `document.cookie` setter 捕获 `__zse_ck=xxx-xxx`；
- node:vm `new Script(v4js).runInContext(dom.getInternalVMContext())`，3s 超时 race 取 token；
- 全局吞 `RuntimeError: unreachable`（WASM VM 探测失败分支的 trap，属正常路径）；
- 缓存键 `zhihu:zse-ck:v4:md5(d_c0)`，并发去重（pending map），按 contentExpire 缓存；
- 之后正常签名（x-zse-96 v3）发 API 请求，UA 用生成 token 时的同一个。
- 已知脆弱点：JSDOM 与真浏览器的环境差异若被 VM 探测到会失败——需跟踪 RSSHub 该文件后续修复 commit 作为「知乎升级挑战」的预警雷达。

### 2. 真浏览器播种 + cookie jar 收割（RSSHub PR#22317，未合并但 e2e 验证过）
Playwright 开 `https://www.zhihu.com/`（`domcontentloaded`），`waitForFunction` 轮询 `document.cookie` 直到 `__zse_ck=` 和 `d_c0=` 都出现（10s 超时、300ms 轮询），然后 `context.cookies()` 全量收割 zhihu.com 域 cookie，缓存 30min。冷启动 ~18s，热取 ~2s。**与我们的方案同构，证明路线有效。**

### 3. 登录持久 profile（myagenttool，内容页场景）
`launchPersistentContext` + 一次性人工登录（拿 z_c0），之后所有渲染走该 profile。专治「新鲜自动化浏览器一律 403 + gdxidpyhxdE 打标」。代价：需要登录态，profile 目录要保鲜。

### 4. 40362 的规避（fxzhihu 实证）
- UA 必须完整真实（含 `AppleWebKit/537.36 (KHTML, like Gecko) Chrome/xxx Safari/537.36` 全链），缩写版必死；
- `x-app-za` 头有无不影响（PR#64 实测）；
- cookie 只带 d_c0+z_c0（+正确签名+完整 UA）可以 200，__zse_ck 不是 40362 场景的必要条件——但裸爬（无挑战通过史）场景是必要条件。

### 5. 历史捷径（已失效，存档）
v3 时代 `static.zhihu.com/zse-ck/v3.js` 可正则提静态站点级 `__zse_ck`（`__g.ck||"..."`）。v4 起失效，勿再尝试。

## 对我们方案的验证与补强建议

### 验证（我们做对了什么）
- ✅ 「伪装 UA + stealth 真实浏览器自动过挑战」与 RSSHub#22317 同构，且比 myagenttool 的「新鲜浏览器全灭」结论更进一步（我们的 stealth 配置生效了）；
- ✅ 「裸 HTTP 403 + 挑战页」「无头默认 UA 40362 硬拒」与开源实测完全吻合——**40362 是 UA 维度触发的硬拒，不是挑战失败**，我们换完整 UA 的方向正确；
- ✅ 两层模型（EO 边缘 + 知乎应用层）解释了为什么「过了 zse_ck 还可能 40362」——两关独立。

### 补强建议
1. **加 JSDOM 快速通道**：把 RSSHub#22319 的 `generateZseCk` 移植为日常 __zse_ck 续期器（纯 Node、3s、无浏览器开销），浏览器方案降为兜底（JSDOM 失败/挑战升级时fallback）。注意照搬其 UA 固定 + 缓存 + 并发去重 + WASM trap guard 四个细节。
2. **三元组生命周期管理**：UA / d_c0 / __zse_ck 绑定存储，缓存 30min~2h 复用，过期整组重跑；**严禁跨组混用**（换 UA 必须重跑挑战）。
3. **请求纪律**：签名覆盖 path+query（含 `?` 后全部）；x-zse-96 用所发 cookie 的同一个 d_c0；x-app-za 无所谓可带可不带。
4. **IP 策略**：机房 IP 是高危维度（EO 边缘层），条件允许走住宅/家宽出口；同一 IP 控制请求频率，避免行为维度升档。
5. **40362 排障树**（按实证优先级）：① UA 是否完整浏览器串 → ② cookie 三元组是否同会话新鲜 → ③ 签名是否含 query → ④ IP 是否被拉黑 → ⑤ 换会话重跑挑战。
6. **雷达机制**：watch RSSHub `lib/routes/zhihu/utils.ts` 和 fxzhihu 的 commit——知乎升级挑战时这两个仓库会先炸，等于免费预警。

## 推荐行动

| # | 行动 | 优先级 |
|:--|:--|:--|
| 1 | 拉取 RSSHub master `lib/routes/zhihu/utils.ts` + `lib/routes/zhihu/execlib/x-zse-96-v3.ts` 全文存档到 `zhihu_tool/research/refs/`，作为签名/挑战的参考实现 | 🔴 高 |
| 2 | 实现 JSDOM 版 __zse_ck 生成器（移植 PR#22319），与现有浏览器方案组成双通道：JSDOM 日常、浏览器兜底 | 🔴 高 |
| 3 | 落地三元组（UA+d_c0+__zse_ck）绑定缓存（30min TTL + 失败自动整组刷新） | 🔴 高 |
| 4 | 40362 监控埋点：响应 JSON `error.code==40362` 时按排障树自动处理（先换完整 UA 重试） | 🟡 中 |
| 5 | 评估出口 IP：机房 → 住宅代理的成本/收益（EO 边缘层最硬的维度） | 🟡 中 |
| 6 | watch RSSHub/fxzhihu 仓库 zhihu 相关 commit（挑战升级预警） | 🟢 低 |

---

### 附：信源清单
- RSSHub PR#22319（merged 2026-07-13）JSDOM 生成 __zse_ck — github.com/DIYgod/RSSHub/pull/22319（diff 已存 /tmp/rsshub_22319.diff）
- RSSHub PR#22317（closed）浏览器收割 cookie — github.com/DIYgod/RSSHub/pull/22317（diff 已存 /tmp/rsshub_22317.diff）
- RSSHub Issue#20402 — 知乎路由 403 排障（cookie 三元组/家宽 vs 机房 IP 实证）
- fxzhihu PR#64/#65 — 40362 与 UA/签名 对照实验 — github.com/frostming/fxzhihu/pull/64, /pull/65
- myagenttool PR#1680（merged 2026-08-15）— secng/zse-ck WAF 下新鲜自动化浏览器全灭 + 持久登录 profile 方案 — github.com/perly6185-lab/myagenttool/pull/1680
- x-zse-96 算法出处 — github.com/srx-2000/spider_collection/issues/18
- X-SecNG-Response 头佐证 — github.com/Norconex/committer-sql/issues/17
