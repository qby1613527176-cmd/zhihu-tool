# 知乎移动端 API（api.zhihu.com）调研报告

> 调研日期：2026-11-06 | 调研方式：GitHub 开源项目逆向分析 + 源码阅读

## 结论摘要

知乎移动端 API（api.zhihu.com）与 Web 端（www.zhihu.com/api/v4）**共享同一套 v4 接口路径**，但认证机制截然不同。移动端采用 **Bearer Token + 原生库签名（x-zse-96 v1）**，签名由 Android .so 原生库计算，需 Unidbg 模拟才能在服务端复现；而 Web 端签名（x-zse-96 v2/v3）已可纯软件实现。**关键结论：移动端通道并不比 Web 端更容易突破——签名依赖原生库是最大门槛，但已有开源项目（Hydrogen 的纯 Lua ZSE96 实现、zhihu-plus-plus 的双模式支持）证明 Web 端签名已完全可在纯软件层面解决，移动端通道的额外价值有限。**

---

## 移动端认证与接口现状

### 1. 认证机制：双轨制

知乎存在两套独立的 API 认证体系：

| 维度 | Web 端（www.zhihu.com） | 移动端（api.zhihu.com） |
|------|------------------------|------------------------|
| 认证方式 | Cookie（d_c0） | Bearer Token（OAuth 2.0） |
| 签名算法 | x-zse-96 v2/v3（纯软件可实现） | x-zse-96 v1（原生 .so 库计算） |
| 签名前缀 | `2.0_` | `1.0_` |
| 签名输入 | `"101_3_3.0+" + path + "+" + d_c0` | `path + authorization + uuid + appVersion` |
| 设备绑定 | 无强制 | UUID（如 `AGAcqsfDWRhLBbB5f6a4DsP9GxvvQtHYefs=`） |
| App版本 | 不需要 | 需要（如 `9.41.0`） |
| 实现难度 | ✅ 纯软件（JS/Lua/Python） | ❌ 需 Unidbg 模拟原生库 |

#### Web 端签名（ZSE96 v3）核心逻辑

来自 Hydrogen 项目的完整 Lua 实现（`services/api/zse96.lua`）：

```lua
-- 签名输入：cookie 中的 d_c0 + URL 路径
local dataToHash = "101_3_3.0+" .. path .. "+" .. dc0
local md5Str = md5(dataToHash)

-- 签名输出：自定义 SM4-like 加密 + 自定义 Base64
headers = {
  ["x-zse-93"] = "101_3_3.0",
  ["x-zse-96"] = "2.0_" .. XZSE96V3:b64encode(md5Str),
  ["x-app-za"] = "OS=Web",
  ["cookie"] = cookie
}
```

算法细节：
- **32 轮自定义分组加密**（类 SM4），含自定义 S-Box（zb 数组，256 字节映射表）和密钥调度（zk 数组，32 个 32 位常量）
- **CBC 模式**，IV 由 keyPad 和明文头异或后经一个加密块推导
- **自定义 Base64 编码表**（非标准字符序）：`6fpLRqJO8M/c3jnYxFkUVC4ZIG12SiH=5v0mXDazWBTsuw7QetbKdoPyAl+hN9rgE`
- 每次请求 seed 随机（0-126），同一 URL 每次签名不同

**关键发现——api.zhihu.com 路径处理**：
```lua
if url:find("https://api.zhihu.com") then
  path = "/api/v4" .. url:match("zhihu.com(.+)")
  targetUrl = "https://www.zhihu.com" .. path
end
```
即 `api.zhihu.com/xxx` 在签名时会被转为 `www.zhihu.com/api/v4/xxx`，**说明两端共用同一套 v4 API 路径体系**。

#### 移动端签名（x-zse-96 v1）

来自 lostjay/zhihu_android_crawler 的 Java 测试代码：

```java
// 签名输入参数
String path = "/api/v4/search_v3?gk_version=gz-gaokao&q=...";
String authorization = "Bearer 2.1vLf…O5RA";
String uuid = "AGAcqsfDWRhLBbB5f6a4DsP9GxvvQtHYefs=";
String appVersion = "9.41.0";

// 签名输出
String expected = "1.0_TkDh3rIMXxdWckHYS6pccb3Jyk3QsRruBL+9QUVjK5wx1D5noh1y8FFwOXm7yPg2";
String sign = emulator.getSign(path, authorization, uuid, appVersion);
```

- 签名由 Android 原生 `.so` 库计算，项目使用 **Unidbg**（ARM 模拟器）在 JVM 上模拟执行原生代码
- 需要 AES 加密作为签名过程的一部分
- 签名服务暴露为 HTTP 端点（`http://localhost:17007/sign`），Python 客户端通过 HTTP 调用获取签名

### 2. 移动端登录流程

移动端 OAuth 登录方式（来自 zhihu-plus-plus README）：
- **手机验证码登录**：发送短信验证码 → 换取 Bearer Token
- **扫码登录**：通过已登录手机扫码 → 获取 Token
- **Cookie 导入**：手动设置 Cookie 登录（Web 兼容模式）

---

## 与 Web 端对比

### 接口覆盖度

| 功能 | Web v4（www.zhihu.com/api/v4） | 移动端（api.zhihu.com） |
|------|------|------|
| 搜索 | ✅ `/api/v4/search_v3` | ✅ 同路径 |
| 回答列表 | ✅ `/api/v4/questions/{id}/answers` | ✅ 同路径 |
| 用户信息 | ✅ `/api/v4/members/{id}` | ✅ 同路径 |
| 推荐 Feed | ✅ 支持 | ✅ 支持（可能有不同推荐策略） |
| 热榜 | ✅ 支持 | ✅ 支持 |
| 评论 | ✅ 支持 | ✅ 支持 |
| 写回答 | ✅ 支持 | ✅ 支持 |
| 想法/Pin | ✅ 支持 | ✅ 支持 |
| 知乎日报 | ✅ 支持（daily.zhihu.com 独立接口） | ✅ 支持 |
| 推送通知 | ❌ 无 | ✅ 移动端特有 |
| 设备注册 | ❌ 无 | ✅ 移动端特有 |

**核心结论：两端 v4 接口路径完全一致，移动端没有独有的内容获取接口。差异主要在认证方式和设备相关功能（推送、设备管理）。**

### 风控对比

| 维度 | Web 端 | 移动端 |
|------|--------|--------|
| 触发条件 | 频繁请求 / 异常 Cookie | 频繁请求 / 异常设备指纹 |
| 验证方式 | 浏览器验证码（滑块/点击） | 可能更强：短信验证 / 设备验证 |
| 封锁粒度 | Cookie/IP | Token/UUID/设备指纹 |
| 恢复难度 | 换 Cookie 或 IP 即可 | 可能需要换设备标识 |
| 403 处理 | 返回 JSON 含 redirect URL | 类似 |
| 额外检测 | User-Agent、Referer | User-Agent、App 版本、UUID、设备型号 |

来自 Hydrogen `network.lua` 的风控处理代码：
```lua
if code == 403 then
  -- 触发安全验证，暂停所有请求
  canLoad = false
  -- 弹窗引导用户完成浏览器验证
  -- 验证完成后需重启应用
```

**评估：移动端风控不一定更弱。** 移动端多了设备指纹维度，且 Token 被封后需要重新走 OAuth 流程（比换 Cookie 更麻烦）。Web 端的 Cookie 池策略在移动端对应的是 Token 池，但获取 Token 的成本更高。

---

## 开源实现清单

### 活跃维护项目

| 项目 | 星数 | 语言 | 最后更新 | 说明 |
|------|------|------|----------|------|
| [zly2006/zhihu-plus-plus](https://github.com/zly2006/zhihu-plus-plus) | 4423★ | Kotlin Multiplatform | 2026-11-04 | **最活跃的第三方客户端**，支持 Web/Android 双模式，ZSE96 v2 纯软件实现，有桌面版 |
| [zhihulite/Hydrogen](https://github.com/zhihulite/Hydrogen) | 1500+★ | Lua (LuaJVM) | 维护放缓 | 完整 ZSE96 v3 纯 Lua 实现，推荐转向 zhihu-plus-plus |
| [eltavine/Zhihu-Hyperion](https://github.com/eltavine/Zhihu-Hyperion) | 155★ | Android | 2026-11-02 | AI 驱动的第三方客户端（zhihu-plus-plus fork） |
| [zhihu-plus/android](https://github.com/zhihu-plus/android) | 18★ | Android | 2026-10-28 | Zhihu+ 客户端 |
| [kangyun1994/zhihu-plus-plus-swift](https://github.com/kangyun1994/zhihu-plus-plus-swift) | - | Swift | - | iOS 版 |
| [zhuoyi233/zhihu-plus-plus-HMOS](https://github.com/zhuoyi233/zhihu-plus-plus-HMOS) | - | ArkTS | - | 鸿蒙版 |

### 逆向/爬虫工具

| 项目 | 星数 | 语言 | 说明 |
|------|------|------|------|
| [lostjay/zhihu_android_crawler](https://github.com/lostjay/zhihu_android_crawler) | 7★ | Java+Python | **专注 Android 签名逆向**，Unidbg 模拟原生库，提供 HTTP 签名服务。公开代码不完整，完整版需凭据 |
| [CharlesPikachu/DecryptLogin](https://github.com/CharlesPikachu/DecryptLogin) | 2851★ | Python | 多网站模拟登录库（含知乎 Web 端） |
| [zkqiang/zhihu-login](https://github.com/zkqiang/zhihu-login) | 355★ | Python | 知乎 Web 模拟登录，支持验证码 |
| [wycm/zhihu-crawler](https://github.com/wycm/zhihu-crawler) | 918★ | Java | 分布式知乎爬虫（Web 端，较老） |

### 关键代码资源

| 资源 | 位置 | 价值 |
|------|------|------|
| ZSE96 v3 完整实现 | Hydrogen `services/api/zse96.lua` | **纯软件实现 Web 端签名，可直接移植为 Python** |
| 请求头管理 | Hydrogen `core/constants.lua` | APP/Web 双模式请求头定义 |
| Android 签名服务 | lostjay 项目 `ZhihuAndroidAppEmulatorTest.java` | Android 签名的输入输出规格 |
| 双模式网络层 | Hydrogen `services/api/network.lua` | 签名触发逻辑 + 风控处理 |

---

## 可行性评估

### 方案 A：Web 端签名（推荐 ✅）

**原理**：使用 Cookie + ZSE96 v2/v3 纯软件签名，直接请求 `www.zhihu.com/api/v4/` 接口。

- **优势**：
  - 签名算法已完全被逆向（Hydrogen 提供了完整 Lua 实现，可移植为 Python）
  - 不依赖原生库，纯软件可运行
  - Cookie 获取简单（浏览器登录后导出）
  - 风控恢复成本低（换 Cookie/IP 即可）
- **劣势**：
  - Cookie 有有效期，需定期更新
  - 某些接口可能要求登录态
- **实现路径**：将 Hydrogen 的 Lua ZSE96 移植为 Python（算法已完整公开），或使用现成的 Python 实现

### 方案 B：Android 签名模拟（可行但复杂 ⚠️)

**原理**：通过 Unidbg 模拟 Android 原生库计算 x-zse-96 v1 签名，使用 Bearer Token 调用 api.zhihu.com。

- **优势**：
  - 模拟真实 App 行为，理论上风控容忍度可能略高
  - 可以访问移动端特有的功能（如推送）
- **劣势**：
  - 需要 JVM 环境运行 Unidbg
  - 需要提取知乎 APK 中的 .so 文件
  - 需要维护签名服务（Java HTTP 服务）
  - Token 获取和管理更复杂
  - lostjay 的完整实现不公开
- **实现路径**：参考 lostjay/zhihu_android_crawler 的架构，自行提取 .so 并用 Unidbg 加载

### 方案 C：模拟器 + 抓包（辅助手段）

**原理**：在 Android 模拟器中运行知乎 App，通过 mitmproxy 抓包分析接口。

- **可行性**：中等
  - Android 7+ 需要安装用户 CA 证书（可在模拟器上操作）
  - 知乎 App 可能使用 SSL Pinning → 需要 Frida/Xposed 绕过
  - 或者使用已 root 的模拟器（如 Android Studio 的 Google Play 镜像不可 root，但 AOSP 镜像可以）
- **用途**：主要用于发现新接口和验证签名逻辑，不适合作为长期数据通道
- **工具链**：Android Emulator + mitmproxy + Frida（绕过 SSL Pinning）

### 方案 D：直接使用第三方客户端 API 层（最省力 ✅）

**原理**：直接复用 zhihu-plus-plus 的 API 调用层。

- zhihu-plus-plus 已经实现了完整的双模式 API 调用
- 代码是 Kotlin Multiplatform，核心逻辑可以跨平台
- 但将 Kotlin 代码集成到 Python 工作流中需要额外桥接

---

## 推荐行动

### 结论：移动端通道不是更优路径

1. **优先继续使用 Web 端 v4 API + ZSE96 签名**。Hydrogen 的 Lua 实现证明 Web 端签名已完全可纯软件解决，且两端共享同一套接口路径。移动端通道不提供额外的内容获取能力。

2. **将 Hydrogen 的 ZSE96 Lua 实现移植为 Python**。这是最直接的行动项：
   - 算法完整（自定义 SM4-like + 自定义 Base64）
   - 输入明确（d_c0 cookie + URL path）
   - 可集成到现有爬虫框架

3. **移动端方案作为备用**：如果 Web 端风控加剧到 Cookie 池无法维持的程度，再考虑：
   - 用 Unidbg 搭建签名服务（参考 lostjay 方案）
   - 或通过 Frida hook 真机/模拟器上的知乎 App 来生成签名

4. **不建议投入模拟器+抓包方案**：维护成本高，且知乎 App 持续更新会导致逆向反复过时。

### 风险提示

- 知乎可能随时更新签名算法版本（当前 ZSE96 已到 v3，v1 用于移动端）
- 移动端 Token 有设备绑定，批量获取 Token 可能触发风控
- 逆向和模拟登录存在法律灰色地带，需注意合规

---

## 参考来源

| 来源 | 说明 |
|------|------|
| [zly2006/zhihu-plus-plus](https://github.com/zly2006/zhihu-plus-plus) | 4423★，最活跃第三方客户端，双模式支持 |
| [zhihulite/Hydrogen](https://github.com/zhihulite/Hydrogen) | ZSE96 v3 纯 Lua 完整实现（`services/api/zse96.lua`） |
| [lostjay/zhihu_android_crawler](https://github.com/lostjay/zhihu_android_crawler) | Android 签名逆向，Unidbg 方案 |
| Hydrogen `core/constants.lua` | APP/Web 双模式请求头定义 |
| Hydrogen `services/api/network.lua` | 签名触发逻辑 + 403/401 风控处理 |
