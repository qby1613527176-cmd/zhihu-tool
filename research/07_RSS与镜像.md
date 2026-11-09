# 07 RSS 与镜像：不爬知乎拿数据的替代源调研

> 调研时间：2026-11-06 ｜ 方法：web_fetch/curl（GitHub API、RSSHub 仓库、公共实例实测；tavily/web_search 不可用）
> 本机网络限制：docs.rsshub.app、web.archive.org、archive.ph、rsshub.app 均不可达（连接超时/拒绝），相应条目已标注"未实测/不可达"。

## 结论摘要

1. **RSSHub 知乎路由 2026 年仍活跃维护**（2026-02 ~ 2026-10 连续有修复合并，最近一次 2026-10-14），但分裂成两个命运截然不同的阵营：
   - **日报系**（`/zhihu/daily`、`/zhihu/weekly`）：走知乎日报旧 API（news-at.zhihu.com），**无反爬**，公共实例实测 HTTP 200 直接可用 ✅
   - **主站系**（hot/question/people/*/zhuanlan/collection/topic/bookstore 等约 15 条）：受知乎 `x-zse-96 v3` 签名 + `__zse_ck` 反爬保护，**公共实例实测普遍 503** ❌；自建需配 `ZHIHU_COOKIES`（d_c0 + __zse_ck）或依赖 2026-03/07 合并的 JSDOM 自动签权，但实测公共实例仍挂、用户反馈 cookie 也间歇 403（issue #23511）
2. **公共 RSSHub 实例对知乎主站不可靠**：实测 rssforever（主站路由全 503）、woodland.cafe（403）、ktachibana.party（路由 404）；官方 rsshub.app 本机不可达。
3. **archive.org / archive.ph 本机不可达，未能实测密度**；定性判断：知乎 2021 年起登录墙，新快照多为登录页，问答长尾覆盖有限——**待可达网络复核**（行动项）。
4. **无系统性知乎镜像站存活**：搜狗知乎搜索已随搜狗并入腾讯死亡；未发现其他成体系镜像；聚合转载不构成可用源。
5. **与直连方案不构成独立冗余**：RSSHub 主站路由和直连撞的是同一堵反爬墙（同样的 __zse_ck/x-zse-96）。其真正价值是——① 日报/周刊这条无墙旁路；② 作为反爬对策的**社区参照实现与先行指标**（RSSHub 仓库里 `execlib/x-zse-96-v3.ts` 就是签名实现，issue/PR 流是知乎反爬变化的早期预警）。

## RSSHub知乎路由存活表

数据来源：① RSSHub 仓库 `lib/routes/zhihu/` 目录（2026-11-06 拉取，17 个路由文件/目录）② 公共实例 `rsshub.rssforever.com` 实测 ③ 路由文件内 `features` 配置。

| 路由 | 仓库路径 | rssforever 实测 | antiCrawler | 需 Cookie | 备注 |
|---|---|---|---|---|---|
| `/zhihu/daily` | daily.ts | ✅ **200 (432KB)** | 否 | 否（requireConfig: false） | 知乎日报，旧 API，**最稳的旁路** |
| `/zhihu/weekly` | weekly.ts | ✅ **200 (3.5KB)** | 否* | 否 | 知乎周刊，同属日报 API |
| `/zhihu/daily/section/:id` | daily-section.ts | ❌ 503 | — | — | 日报栏目，实测挂 |
| `/zhihu/hot/:category?` | hot.ts | ❌ 503 | **是** | 可选（ZHIHU_COOKIES） | 热榜；`/zhihu/hotlist` 301 重定向至此 |
| `/zhihu/question/:qId/:sortBy?` | question.ts | ❌ 503 | **是** | 可选（d_c0+__zse_ck 跳过初始化；否则 Workers 用 Playwright、Docker/Vercel 用 JSDOM 生成凭证） | 问题回答流 |
| `/zhihu/people/answers/:id` | answers.ts | ❌ 503 | 是 | 可选 | 用户回答 |
| `/zhihu/people/activities/:id` | activities.ts | ❌ 503 | 是 | 可选 | 用户动态；2026-10-07 有 403 issue（#23511，当日关闭） |
| `/zhihu/people/posts/:id` | posts.ts | 未实测 | 是 | 可选 | 用户文章 |
| `/zhihu/people/pins/:id` | pin/ | 未实测 | 是 | 可选 | 想法；2026-10-14 刚修空列表 bug（#23574 merged） |
| `/zhihu/people/collections/:id` | all-collections.ts | 未实测 | 是 | 可选 | 用户全部收藏夹 |
| `/zhihu/zhuanlan/:id` | zhuanlan.ts | ❌ 503 | **是**（但 requireConfig: false） | 否 | 专栏；无 cookie 要求仍被反爬挡 |
| `/zhihu/collection/:id` | collection.ts | ❌ 503 | 是 | 可选 | 收藏夹 |
| `/zhihu/topic/:id` | topic.ts | ❌ 503 | 是 | 可选 | 话题 |
| `/zhihu/bookstore/newbook` | bookstore/ | ❌ 503 | — | — | 知乎书店 |
| `/zhihu/timeline` | timeline.ts | 未实测 | 是 | **必需** | 个人时间线，需登录态 |
| `/zhihu/xhu/*`（8 条） | xhu/ | 未实测 | — | 是（含 auth.ts） | 新版命名空间：activities/answers/collection/posts/question/topic/zhuanlan，独立鉴权逻辑 |
| （支撑文件） | execlib/x-zse-96-v3.ts、utils.ts、browser.ts、sign.ts、check-cookie.ts | — | — | — | **反爬签名实现就在仓库里**，可直接参考 |

### 维护活跃度时间线（GitHub PR/issue 实证）

| 日期 | 事件 | 状态 |
|---|---|---|
| 2026-02/03 | daily 路由迁移 v4 API（#21252/#21253/#21255） | merged |
| 2026-03-01 | 修 answers/zhuanlan 图片缺失（#21257） | merged |
| 2026-03-07 | **无 cookie 时自动获取 `__zse_ck`（#21321）** | ✅ merged |
| 2026-06-21 | 浏览器 session 取 `__zse_ck`（#22317） | ❌ 被拒（DO NOT merge） |
| 2026-07-13 | **JSDOM 生成 `__zse_ck`（#22319）** | ✅ merged（测试被绕过，可靠性存疑） |
| 2026-07-04 | org posts 改用 /members/ API（#22425） | merged |
| 2026-09-11 | daily 日期缺失修复（#23276） | merged |
| 2026-09-25 | "use old api"（#23393） | merged |
| 2026-10-07 | 用户动态 403 issue（#23511，PikaPods 部署，cookie 无误仍 403） | 当日 closed/completed |
| 2026-10-14 | pins 空列表 503 修复（#23574） | merged |

**判读**：社区在与知乎反爬持续拉锯——路由不死，但"能跑"依赖不断更新的签名/凭证逻辑；公共实例跟不上这个节奏，所以普遍挂；自建 + 最新代码 + cookie 才有可用性，且仍有间歇 403 的实证。

## 存档/镜像源清单

| 源 | 状态 | 实测结果 | 评价 |
|---|---|---|---|
| web.archive.org（Wayback） | ⚠️ 本机不可达 | 连接超时，CDX/availability API 均未通 | 定性：知乎 2010 年起有快照；2021 年登录墙后新快照多为登录/验证页；热门问答页有存档，长尾问题几乎无。**密度需可达网络复核** |
| archive.today (archive.ph) | ⚠️ 本机不可达 | HTTP 000 | 历史上可存档知乎单页，需人工逐页提交，无系统性覆盖 |
| 搜狗知乎搜索 | ❌ 已死 | — | 搜狗并入腾讯后下线 |
| 系统性知乎镜像站 | ❌ 无存活 | — | 历史镜像均已关停；本调研未发现任何成体系替代 |
| 知乎日报官方 API（news-at.zhihu.com） | ✅ 活 | RSSHub daily/weekly 路由 200 实证 | **唯一无反爬的官方旁路**，但只有日报内容，不覆盖主站问答 |
| 聚合/转载（cnblogs、SegmentFault 等转载专栏） | ⚠️ 零散 | — | 非系统源，覆盖率不可控，不可用 |

## 与直连方案的互补性

| 维度 | 判断 |
|---|---|
| 反爬冗余 | **不成立**。RSSHub 主站路由与直连撞同一堵墙（`__zse_ck` / `x-zse-96 v3`），直连被挡时 RSSHub 同样被挡，两者正相关失效 |
| 无墙旁路 | **成立但窄**。日报/周刊走旧 API 无反爬，可作内容补充，但不含主站问答/热榜/用户数据 |
| 预警价值 | **成立且独特**。RSSHub issue/PR 流（如 #23511 的 403 报告、#21321/#22319 的签权方案迭代）是知乎反爬变化的**先行指标**；仓库 `lib/routes/zhihu/execlib/x-zse-96-v3.ts` 是现成的签名参照实现 |
| 部署成本 | 公共实例不可依赖（配额+反爬双重不稳）；自建需 Docker + cookie 维护 + 跟进签名更新，成本接近直连本身的维护 |
| 存档兜底 | 弱。archive.org 受登录墙所限，对 2021 后内容覆盖差（待复核） |

**定位建议**：RSS/镜像通道**不是直连的备胎，而是直连的仪表盘**——用它监测知乎反爬变化、参照签名实现，外加日报这条无墙小旁路。

## 推荐行动

1. **自建 RSSHub 验证组**（优先级中）：Docker 部署 + 配置 `ZHIHU_COOKIES`（d_c0 + __zse_ck），跑最新 master（含 #21321/#22319 自动签权），订阅 `/zhihu/daily`、`/zhihu/weekly` + 2~3 条主站路由，记录一周可用率——验证"自建+cookie"在我们网络下的真实水位。
2. **监控 RSSHub zhihu 目录作预警**（优先级高，成本低）：watch 仓库 `lib/routes/zhihu/` 的 commit 与含 "zhihu" 的 issue；每次知乎端反爬升级，RSSHub 社区通常 1~2 周内出修复 PR——直连方案可同步跟进其签名实现（`execlib/x-zse-96-v3.ts`）。
3. **补做 archive.org 密度验证**（优先级低）：在可达网络跑 CDX 查询 `zhihu.com/question/*` 2020–2026 快照密度与状态码分布，再决定它能否进入存档兜底层。
4. **日报 API 直接利用**（优先级低）：`news-at.zhihu.com` 可匿名访问，若需要日报内容可绕过 RSSHub 直连该 API。
5. **不投入镜像站搜寻**：已确认无存活系统镜像，此方向关闭。
