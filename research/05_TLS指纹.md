# R5 调研：TLS/HTTP 指纹伪装路线（curl-impersonate / tls-client / JA3 伪造）

> 调研日期：2026-11-06 · 调研方式：GitHub API + 官方文档 + 真实案例源码（web_fetch/curl，~10 次 fetch）
> 背景问题：纯 HTTP 抓知乎死于 `zse_ck` JS 挑战 + EdgeOne。若有库能完美伪装 TLS 指纹，纯 HTTP 能否复活（更快更轻）？

## 结论摘要

**不能复活。TLS 指纹伪装是真实有效的一层，但它只解决"网络层身份"，解决不了"JS 挑战"这一层——而我们的死因恰恰是后者。**

三个关键证据：

1. **curl_cffi 官方 FAQ 原话**："TLS and http2 fingerprints are just one of the many factors Cloudflare considers. Other factors include IP quality, request rate, **JS fingerprints**… for higher levels, you may need **browser automation tools like playwright**。"作者自己另开了 [brimp](https://github.com/lexiforest/brimp)（轻量浏览器运行时）专门解决 JS 挑战——等于官方承认纯 TLS 伪装过不了 JS 挑战。
2. **真实知乎案例实测**：[astrbot_plugin_zhihu_parse](https://github.com/HGXY2004/astrbot_plugin_zhihu_parse)（2026-09 仍活跃）T1 层用 curl_cffi 三档指纹（chrome/safari_ios/chrome_android）直连，**实测阿里云数据中心 IP 三层全部命中 `__zse_ck` 挑战失败**，必须降级到 T2 的 JS 求解反代；只有住宅 IP 下 T1 才通常成功。
3. **知乎的 `__zse_ck` 本质是 WASM PoW**：混淆 JS 虚拟机 + 内嵌 WASM 工作量证明，必须执行 JS 算出 `__g.ck` 值写 cookie——纯 TLS 伪装在协议层再完美，也到不了"执行 JS"这一步。

**但本次调研有重大副产品**：发现了 [zhihu-proxy](https://github.com/HGXY2004/zhihu-proxy) 项目——**无浏览器、纯 Node vm 沙箱跑知乎官方 JS** 的中间路线，已实测解决 `x-zse-96` 签名 + `__zse_ck` 挑战（005_ 真实 PoW 值），单次签名 <1ms。这是比 Playwright 轻得多的"准纯 HTTP"方案，建议作为独立候选路线（见文末"推荐行动"）。

## TLS 指纹在 Bot 检测中的角色

### 机制（JA3/JA4）

- TLS 握手第一个包 ClientHello 就暴露客户端身份：cipher suites 列表/顺序、扩展列表/顺序、椭圆曲线、ALPN、GREASE 等。Chrome 用 BoringSSL、Firefox 用 NSS、Python 用 OpenSSL——参数差异巨大（例：Chrome 16 个 cipher suites vs Python 43 个）。
- **JA3**（Salesforce）：`MD5(SSLVersion,Cipher,SSLExtension,EllipticCurve,EllipticCurvePointFormat)`，Wireshark 已内置。
- **JA4**（FoxIO）：JA3 的改进，排序扩展降低现代浏览器指纹熵，附带 JA4 Signals（h2h3_ratio、heuristic_ratio、browser_ratio、reqs_rank 等行为统计）。
- 还有 HTTP/2 指纹（SETTINGS 帧、窗口大小、优先级——Akamai 指纹）和 HTTP/3/QUIC 指纹。

### 在现代 Bot 检测中的权重

TLS 指纹是**必要不充分**条件，是多层防御的第一层：

| 层级 | 检测内容 | TLS 伪装能否解决 |
|---|---|---|
| L1 网络层 | JA3/JA4、HTTP/2 Akamai 指纹、ALPN | ✅ 这就是 curl-impersonate 的核心能力 |
| L2 IP 声誉 | 数据中心/住宅 IP、ASN、历史行为 | ❌ 需代理 |
| L3 HTTP 层 | Header 顺序/取值、cookie 一致性 | ✅（curl-impersonate 连 header 都模拟） |
| L4 **JS 挑战** | JS 执行、WASM PoW、DOM/BOM 环境探测 | ❌ **需要 JS 引擎** |
| L5 行为层 | 鼠标/时序/请求模式 | ❌ |

Cloudflare 官方文档确认 JA3/JA4 只在 **Enterprise Bot Management** 付费版可用，且 JA4 Signals 包含大量行为统计（reqs_quantile_1h、ips_rank_1h 等）——说明即使过了指纹匹配，还有行为维度。知乎侧同理：EdgeOne + 自有风控（40362 请求异常限制、40353 need_login）。

## 工具清单

| 工具 | 语言 | Stars | 最近提交 | 最新 Release | 状态评估 |
|---|---|---|---|---|---|
| [lexiforest/curl-impersonate](https://github.com/lexiforest/curl-impersonate) | C/Python(build) | 2,861 | 2026-10-29 | v2.3.2 (2026-10-30) | ✅ 高度活跃，lwthiker 原版的活跃 fork，支持 Chrome 151/152、Safari 27、Firefox 153–155，含 ECH/ZSTD/MLKEM/HTTP3 指纹 |
| [lexiforest/curl_cffi](https://github.com/lexiforest/curl_cffi) | Python 绑定 | 6,817 | 2026-10-27 | v0.17.0 | ✅ 最主流 Python 方案，`impersonate="chrome"` 一行接入，API 兼容 requests |
| [bogdanfinn/tls-client](https://github.com/bogdanfinn/tls-client) | Go | 1,929 | 2026-11-02 | v1.16.0 (2026-09-02) | ✅ 活跃，net/http.Client 风格 API，基于 utls |
| [refraction-networking/utls](https://github.com/refraction-networking/utls) | Go 库 | 2,628 | 2026-11-03 | v1.8.2 (2026-01-13) | ✅ 活跃，底层库（tls-client 依赖它），需自己组装指纹 |
| JA3Transport | Go | — | 久未更新 | — | ⚠️ 只伪造 JA3 不管 HTTP/2 指纹，不完整 |

**能力共性**：都能让 TLS ClientHello + HTTP/2 握手与真实浏览器逐字节一致（可用 `tls.browserleaks.com/json` 验证）。**共性边界**：都不含 JS 引擎，过不了任何需要执行 JS 的挑战。

## 纯 HTTP 过 JS 挑战的边界论证

### 论证一：JS 挑战的定义就是"必须执行 JS"

知乎 `__zse_ck` 挑战流程（据 zhihu-proxy 逆向文档）：
1. 服务器 403 返回挑战页：`<meta id="zh-zse-ck">` + `static.zhihu.com/zse-ck/v4/*.js`
2. 该脚本是**混淆 JS 虚拟机 + 内嵌 WASM PoW**，计算 `__zse_ck = __g.ck + '-' + meta.content`
3. 写入 cookie 后刷新页面才放行

`__g.ck` 只在 JS/WASM 运行时产生。TLS 握手在这个阶段**早已完成**——伪装得再完美的 ClientHello 也不会让服务器跳过挑战页。同理 Cloudflare 的 JS Challenge / Turnstile、EdgeOne 的 JS 验证。

### 论证二：工具作者自己的立场

- curl_cffi FAQ："Can I bypass Cloudflare? Short answer: **it depends**… for the most basic ones, TLS fingerprints alone maybe enough, but for higher levels, you may need… **browser automation**"
- curl_cffi README 把 JS 挑战引流到独立项目 brimp（"Scalable browser runtime for agents"，2026-08 创建）——作者用开新项目的方式投票：**纯 HTTP 路线对 JS 挑战无解**
- GitHub issue [#463](https://github.com/lexiforest/curl_cffi/issues/463)「请问怎么绕过 cloudflare 的真人验证」→ 维护者直接指向上述 FAQ 并关闭

### 论证三：知乎真实案例（最有说服力）

[astrbot_plugin_zhihu_parse](https://github.com/HGXY2004/astrbot_plugin_zhihu_parse) 降级链设计（2026-09 实测）：

```
T1 direct/desktop   curl_cffi impersonate=chrome        ─┐
T1 direct/ios       curl_cffi impersonate=safari_ios     ├ TLS 指纹伪装直连
T1 direct/mobile    curl_cffi impersonate=chrome_android ─┘
T2 proxy/extract    zhihu-proxy（x-zse-96 签名 + __zse_ck WASM 挑战求解）
```

**实测结果（阿里云数据中心 IP）：T1 三档指纹全部命中 `__zse_ck` 挑战 → 失败**；T2 成功。住宅 IP 下 T1 第一层即成功。
→ 结论：**TLS 指纹伪装决定不了成败，IP 声誉 + JS 挑战求解才决定**。TLS 伪装只是入场券，知乎风控在数据中心 IP 上直接发挑战，伪装无效。

## 对我们的意义

1. **R5 路线（纯 HTTP + TLS 伪装复活论）证伪**：我们的死法是 `zse_ck` JS 挑战 + EdgeOne，TLS 伪装解决不了 JS 执行，此路不通。不需要再投入。
2. **TLS 伪装不是毫无价值**——它是"达标线"而非"决胜项"：如果未来任何 HTTP 通道（如 zhihu-proxy 式反代）被用到，用 curl_cffi 替代 requests 属于零成本加固（指纹不对会被秒拒）。作为组件价值存在，作为路线价值不存在。
3. **重大副产品——zhihu-proxy 中间路线**：该项目证明了一条我们调研中未覆盖的路线：**不起浏览器，用 Node vm 沙箱直接运行知乎官方 JS**（x-zse-96 签名用官方 webpack 模块，`__zse_ck` 用沙箱补浏览器环境跑官方挑战脚本）。特征：
   - 零第三方依赖，Node ≥18 原生，单次签名 <1ms，挑战求解秒级
   - 官方 JS 升级只需替换 vendor 文件，不做算法重写（维护成本低）
   - 实测数据中心 IP：文章 API 通道（匿名 d_c0+签名）✅；回答/问题/热榜需登录 z_c0；**HTML 页面通道仍被 IP 声誉层拦（40362）**——即它也承认机房 IP 的 HTML 通道无救
   - 比 Playwright 轻一个数量级，比纯 requests 多一个 Node 进程
4. **IP 声誉是隐藏天花板**：zhihu-proxy 文档明确"住宅 IP / 登录态下 HTML 通道可用，机房 IP `__zse_ck` 已正确求解仍 40362"。任何路线若不换 IP 属性，上限都被 IP 声誉锁死。

## 推荐行动

| 优先级 | 行动 | 理由 |
|---|---|---|
| 🔴 立即 | **放弃 R5 纯 HTTP+TLS 伪装路线**，调研闭环 | JS 挑战边界已三重论证（机制/作者立场/知乎实测），无需再试 |
| 🟡 高 | **立项评估 R6：zhihu-proxy 式 Node vm 沙箱路线** | 已有人 2026-09 实测跑通 `x-zse-96` + `__zse_ck`，代码零依赖可审计（HGXY2004/zhihu-proxy，1 star 但完成度高）；它覆盖的"文章 API 匿名通道"正好是我们纯 HTTP 想要的场景 |
| 🟡 高 | **R6 评估时先验证适用范围**：它只保证文章 API 匿名可用；回答/热榜仍需登录 cookie——与我们的目标资源清单对齐再决定 | 避免重复"跑通了但抓不到目标内容" |
| 🟢 低 | curl_cffi 纳入工具箱备用（`pip install curl_cffi`，`impersonate="chrome"`） | 未来任何直连通道的零成本加固件；注意它过不了挑战，只过指纹 |
| 🟢 低 | 记录 IP 声誉结论：机房 IP 对知乎 HTML 通道基本无救，住宅 IP 代理是所有路线的共同前置条件 | 影响所有后续路线的架构决策 |

### 附：关键引用

- curl_cffi FAQ: https://curl-cffi.readthedocs.io/en/latest/faq.html （"Can I bypass Cloudflare" 节）
- lwthiker TLS 指纹原理: https://lwthiker.com/networks/2022/06/17/tls-fingerprinting.html
- Cloudflare JA3/JA4 文档: https://developers.cloudflare.com/bots/additional-configurations/ja3-ja4-fingerprint/
- 知乎实测案例: https://github.com/HGXY2004/astrbot_plugin_zhihu_parse （降级链 + 实测结论在 README）
- zhihu-proxy（副产品发现）: https://github.com/HGXY2004/zhihu-proxy （反爬绕过原理四层文档）
