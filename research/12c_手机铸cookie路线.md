# 12c 手机容器「铸 cookie」替代路线评估（不依赖 PC 浏览器）

> 调研时间：2026-11-06 · 方法：GitHub 仓库/diff/issue 直取 + Termux 官方仓库索引（Packages）+ 各项目官方文档
> 背景：纯 HTTP 通道已通（Python 签名 + 导出 cookie），但 `__zse_ck` 数天轮换；Node 求解器（node:vm 沙箱）能算出真 PoW 值（005_ 前缀）、过挑战页校验，但 API 层 40362 不认——怀疑服务端要求该 d_c0 有「浏览器解题档案」。本文评估 5 条手机端（Termux/proot-distro，ARM64）铸 cookie 路线。
> 评级口径：可行性/反检测强度 ★ 越多越好；**成本 ★ 越多 = 越省**。

---

## 0. 先更新一个关键认知（直接影响全部路线评级）

本次调研抓到 **zhihulite/zhihu_zse_ck**（活跃，25 天前更新）的完整逆向（docs/ANALYSIS.md），把「服务端到底校验什么」从猜测变成了实测：

- `__zse_ck` 本体 = `005_ + radix64(encrypt_96(record93))`，record93 内含 **12×16 位位图（环境检测结果）** + 32hex 指纹 + crc32(UA) + tail；
- **服务端逐字段扫描实测**：`w4` 位图字是会话判据——`w4=0x4000` → 302 进真实页；`w4=0x4030` → 进 `/account/unhuman`（**token 有效但会话被反自动化层标记**）。`w4` 的 bit5/bit4 来自 payload 层两条守卫：`setTimeout.toString()` / `setInterval.toString()` 不含 "native" 就置位；
- **指纹值、UA 值本身不参与判定**（同 build 同 meta 只改它们，落点不变）。

推论（两条互不排斥的假设，解释我们的 40362）：
- **H1 位图假设**：我们 vm 沙箱里的 `setTimeout` 是 JS 桩 → `toString()` 不含 `[native code]` → w4 被置 0x4030 → token 自带「非真人」标记 → API 层 40362。**若成立，修沙箱桩的 `toString()` 一行就能解**（成本最低的实验）。
- **H2 遥测假设**（DEPLOY_PHONE.md 原判）：挑战 JS 的遥测 XHR 被沙箱吞掉，服务端无解题档案。若成立，需要「真发 XHR」（路线④）或真浏览器（路线①②③）。

> 下文每条路线的「反检测强度」都按「能否同时满足 H1+H2」来评。

---

## ① Termux 原生 chromium 无头（`pkg install chromium`）

**结论：包真实存在、aarch64 有官方预编译、无头可行，是最省事的真实浏览器方案。**

| 维度 | 评级 | 依据 |
|:--|:--:|:--|
| 可行性 | ★★★ | Termux 官方 x11-repo 收录 `chromium` 包，**aarch64 可用**（build.sh 只排除 i686：`TERMUX_PKG_EXCLUDED_ARCHES="i686"`），版本 156.0.8078.79-1，维护者 @licy183 活跃（chromium-host-tools 自动更新 issue 最近 2026-10-23 关闭） |
| 成本 | ★☆☆ | **实测包数据**（官方 Packages 索引，aarch64）：deb 下载 **152 MiB**，安装后 **647 MiB**；依赖闭包（gtk3/mesa/nss/cups/pango/pulseaudio…）再补 ~250-350 MB 安装 → **合计约 0.9–1.1 GB 存储**。运行时内存：headless 单页典型 200–500 MB RSS（4 GB 手机紧张，6–8 GB 尚可） |
| 反检测强度 | ★★★ | 真 Chromium：全部函数真 native（H1 的 w4 自然 0x4000）、遥测 XHR 真发（H2 满足）、canvas/WebGL 真实输出。⚠️ 唯一软肋：Termux/proot 里 GPU 走 SwiftShader 软渲染，WebGL renderer 字符串是软渲染特征——但按 §0 实测指纹值不参与判定，风险可控 |

**落地命令（无头铸 cookie）：**
```bash
pkg install x11-repo && pkg install chromium   # 需先开 x11-repo
chromium --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --user-data-dir=$HOME/.zck-profile 'https://www.zhihu.com/explore'
# 自动化控制：playwright-core / puppeteer-core 用 executablePath 或 CDP 直连
```
注意点：
- **`--no-sandbox` 必须**：Android 内核/SELinux 不给无特权 user namespace，chromium 沙箱起不来（Termux 生态惯例）；
- 无头不需要 termux-x11/显示服务器；
- 我们 PC 的 Playwright 脚本可平移：把 `executablePath` 指向 `$PREFIX/bin/chromium`（playwright-core，不要让它自己下载浏览器），或 `chromium --remote-debugging-port` + `connectOverCDP`。

---

## ② proot-distro（debian/ubuntu）apt chromium + Playwright ARM64

**结论：官方支持面已齐——Playwright 官方明确支持 Debian/Ubuntu arm64，这是我们「PC Playwright 方案的手机原版移植」，代码改动最小。**

| 维度 | 评级 | 依据 |
|:--|:--:|:--|
| 可行性 | ★★★ | Playwright 官方 System requirements（playwright.dev/docs/intro）：**「Debian 12 / 13, Ubuntu 22.04 / 24.04 / 26.04 (x86-64 or arm64)」**——arm64 是一等公民，`npx playwright install chromium` 在 debian proot 里直接下官方 arm64 构建。⚠️ **Ubuntu 的 apt chromium-browser 是 snap 过渡包**（proot 里 snapd 不可用）——要么用 **Debian**（chromium 是真 deb），要么直接用 Playwright 自带浏览器 |
| 成本 | ★☆☆ | proot-distro debian 根系统 ~0.5 GB + Playwright chromium（full ~170 MB 下载 / headless shell 更小，`--only-shell`）+ Node。合计 ~1 GB+，与路线①同量级；proot 系统调用翻译有少量 CPU 开销 |
| 反检测强度 | ★★★ | 同①（真 Chromium，native 函数 + 真遥测）；SwiftShader 软渲染注意事项同① |

**落地命令：**
```bash
pkg install proot-distro && proot-distro install debian
proot-distro login debian
apt update && apt install -y nodejs npm   # 或装 nvm
npm i playwright-core && npx playwright-core install --with-deps chromium
# 启动必须带沙箱关闭（proot 无 user namespace）：
# chromium.launch({ args: ['--no-sandbox','--disable-dev-shm-usage'], headless: true })
```
与①的差别：① 用 Termux 原生包（bionic 链接，启动快一点）+ CDP 外挂控制；② 用 glibc 发行版 + Playwright 原生 API（`launchPersistentContext` 持久档案直接可用，最贴近 PC 方案）。两者都铸「桌面 UA」cookie——**cookie 绑定 UA**，后续 fetch.py 必须发同一个 UA。

---

## ③ 轻量 WebView 方案（Android WebView 从 Termux 调起）

**结论：没有现成的 Termux→WebView 桥，必须借一个 Android App 壳；换来的是全路线里最真实的「移动 Chrome」指纹。**

| 维度 | 评级 | 依据 |
|:--|:--:|:--|
| 可行性 | ★★☆ | **Termux:API 没有 WebView 能力**（termux-api 只暴露 wifi/sms/传感器等固定命令集，WebView 不在其中）。可行路径都得经过一个 App：**AutoX.js**（Auto.js 的开源社区分支，GitHub 搜 AutoX 近千结果，活跃）可脚本化创建 `android.webkit.WebView`、跑挑战页、用 `CookieManager.getInstance().getCookie(url)` 读出 cookie 写 /sdcard（Termux 经 `termux-setup-storage` 读取）；或自编译一个极简 WebView APK（Termux 内有 aapt2/ecj，重）；DSHBox 类项目证明了「WebView APK + Termux 运行时」模式可行 |
| 成本 | ★★☆ | 零大存储（系统 WebView 现成，AutoX.js APK ~20 MB）；但胶水脆：WebView 要有 Looper/UI 线程（可用无界面 Service 承载），自动化链长，机型适配（MIUI/EMUI 杀后台）是运维坑 |
| 反检测强度 | ★★★ | **手机上真实的 Android System WebView**（真 Chrome 引擎、真硬件 GPU、移动传感器/时区/字体栈）——服务端视角是「一台真手机」，指纹自洽度全场最高。产出的 cookie 绑 **移动 UA**，fetch.py 换用同一移动 UA 即可 |

适合「要移动指纹」的场景；若只是要桌面 UA cookie，①②更直接。

---

## ④ node 全功能 DOM（jsdom 网络开启版 / happy-dom / linkedom）跑挑战 JS 且真发 XHR

**结论：jsdom 是唯一「DOM 完整 + 可真实联网」的轻量选项，是现有 vm 求解器的直接升级；linkedom 无网络栈直接出局。**

| 维度 | 评级 | 依据 |
|:--|:--:|:--|
| 可行性 | ★★★（jsdom）/ ★★☆（happy-dom）/ ✗（linkedom） | **jsdom**：`resources:"usable"` 真加载子资源、自带 XMLHttpRequest 走真实网络、配 CookieJar 可带 cookie——**遥测 XHR 能真发**（H2 可满足）。**RSSHub PR#22319（已合并）实证**：`new JSDOM(..., { runScripts:'outside-only', virtualConsole })` + node:vm 直接跑知乎官方挑战 JS 产可用 `__zse_ck`，3 秒出 token。**happy-dom**：源码树确有完整 `XMLHttpRequest` 实现（src/xml-http-request/）+ Fetch API，但反爬实战案例少。**linkedom**：官方自述只做 SSR DOM、「不要假装浏览器」，**无 XHR/网络层**——出局 |
| 成本 | ★★★ | 纯 npm 包（jsdom 依赖数十 MB），内存 ~100–200 MB，秒级运行；Termux 的 Node（ARM64 官方支持）零障碍 |
| 反检测强度 | ★★☆ | 比 node:vm 沙箱**强的点只在「真发 XHR」**（若 H2 成立则决定性）；DOM/指纹完整度与沙箱同档：jsdom 的 setTimeout 同样是 JS 实现（w4 位照常被置位，**H1 仍需手工伪造 native toString**，但可伪造）；无 canvas/WebGL（指纹 40 字段里这些为空——§0 实测指纹值不参与判定，但 API 层是否校验未知）。**服务端视角：比 vm 沙箱真一档，离真浏览器差两档** |

**与 node:vm 沙箱派的差异总结**：沙箱派（我们现状）= 环境全手工桩、网络全吞；jsdom 派 = 环境由库维护（更接近标准浏览器行为）、网络真实。两者都能跑通挑战拿 005_ 值（RSSHub 已证），差别在 API 风控要的「档案」：H2 世界 jsdom 赢，H1 世界两者打平（都要伪造 native）。

**这是下一步成本最低的实验**：给现有求解器套 jsdom 外壳 + 放行遥测 XHR，同时顺手把桩的 `toString()` 伪造成 `function setTimeout() { [native code] }`（两个假设一起测）。

---

## ⑤ WebAssembly 直跑挑战里的 WASM PoW（提取 WASM 单独跑）

**结论：「提取 WASM 出来跑」这条路本身没有价值——但同源思想的开源先例已经走到更远的终点：不跑 WASM，纯 Python 静态推常量直接算 token。这是长期最优解候选。**

| 维度 | 评级 | 依据 |
|:--|:--:|:--|
| 可行性 | ★☆☆（提取跑）/ ★★★（静态纯算） | **提取 WASM 单独跑≈死路**：挑战内嵌的是 Go 编译的 WASM（Go 字节码机：10565 词程序+104 项取指表），其 syscall/js import 回调 DOM/ navigator/cookie——脱离 JS 环境就得自己实现整套 syscall/js host，工作量≈重写沙箱，**零收益**；开源界无人这么做。**但静态纯算有完整先例**：zhihulite/zhihu_zse_ck 实测——「给一个 build 的 wasm，**不跑它的代码、不跑 wasm、不读内存**，直接用 Python 算出被服务端接受的令牌」；常量（rk/alphabet/xreg/mask/iv）全在 WASM 常量池里可静态解出，build 轮换每次重推即可；**全新随机 build 多次实测 302 进真实页** |
| 成本 | ★★★ | 纯 Python 3.10+，零依赖，毫秒级出 token；Termux Python（ARM64）直接跑 |
| 反检测强度 | ★★☆ | 结构自洽：crc32(UA) 自己算、位图可钉「干净」值（w4=0x4000）、iv 任意 4 字节皆可（实测）；通过服务端重定向校验（已证）。**但**：① 对 HTML 重定向已证，对 API 40362 层未证；② 与沙箱派一样不产生遥测档案（H2 世界同病）；③ 协议结构大改（v5？）会整体失效，需持续跟踪上游 build |

**定位**：如果④的实验确认 H1（位图）才是 API 门槛，⑤就是终局（手机上零浏览器永续铸 cookie）；如果确认 H2（遥测档案），⑤和 vm 沙箱一样差一口气，需①②③补档案。

---

## 总表

| 路线 | 可行性 | 成本（★多=省） | 反检测强度 | 一句话 |
|:--|:--:|:--:|:--:|:--|
| ① Termux chromium 无头 | ★★★ | ★☆☆（~1 GB 存储） | ★★★ | 官方 aarch64 包已就位（152 MB 下载/647 MB 安装），`--no-sandbox` 即可无头 |
| ② proot Debian + Playwright arm64 | ★★★ | ★☆☆（~1 GB+） | ★★★ | PC 方案的原版移植；Playwright 官方支持 arm64；用 Debian 别用 Ubuntu（snap 坑） |
| ③ AutoX.js 托管 WebView | ★★☆ | ★★☆ | ★★★ | 无现成桥需 App 壳；换最真实移动指纹 |
| ④ jsdom 真 XHR（happy-dom/linkedom） | ★★★ / — | ★★★ | ★★☆ | 现有求解器的直接升级；linkedom 无网络出局 |
| ⑤ 静态纯算（zhihulite 路线） | ★★★ | ★★★ | ★★☆ | 提取 WASM 跑=死路；静态推常量+纯 Python=已证终局候选 |

## 建议执行顺序

1. **先做两个零成本实验**（现有求解器上改）：a) 桩函数 `toString()` 伪造 native（测 H1 位图假设）；b) jsdom 外壳 + 放行遥测 XHR（测 H2）。两个实验直接决定哪条假设成立。
2. H1 成立 → 走 **⑤**（接入 zhihulite 纯算流水线，手机上零浏览器永续）；H2 成立 → 走 **②**（proot Debian + Playwright 持久档案，最接近 PC 已验证方案），①为轻量替代。
3. 需要移动指纹时再上 **③**（AutoX.js WebView）。

## 主要信源

- Termux 官方仓库索引 `packages-cf.termux.dev/apt/termux-x11/.../Packages`：chromium 包 aarch64，deb=159,562,664 B，Installed-Size=662,284 KB，v156.0.8078.79-1
- termux/termux-packages `x11-packages/chromium/build.sh`（EXCLUDED_ARCHES=i686，TERMUX_PKG_ON_DEVICE_BUILD_NOT_SUPPORTED）与 chromium-launcher.sh.in
- Playwright 官方文档 docs/intro（System requirements: Debian 12/13, Ubuntu 22.04/24.04 arm64）与 docs/browsers（headless shell / new headless）
- RSSHub PR#22319 diff：JSDOM(`runScripts:'outside-only'`)+node:vm 跑官方挑战 JS（已合并）
- HGXY2004/zhihu-proxy README：vm 沙箱求 005_ 值、40362 机房 IP 声誉层实证
- **zhihulite/zhihu_zse_ck README + docs/ANALYSIS.md**：六层 JS VM + Go WASM 字节码机全逆向、纯算流水线、位图 w4=0x4000/0x4030 判据、iv 任意性实测
- jsdom README（resources:"usable" / runScripts）、happy-dom 源码树（xml-http-request/）、linkedom README（自述不做浏览器伪装）
- termux/termux-api README（固定 API 集，无 WebView）；GitHub 搜索「AutoX」（Auto.js 开源社区分支生态）；clearw5/Auto.js（原版已闭源）
