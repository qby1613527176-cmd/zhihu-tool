# 01 · 知乎 CLI / 爬虫工具生态全景调研

> 调研时间：2026-11-06 ｜ 数据来源：GitHub Search API + 各 repo README（raw.githubusercontent）
> 背景：我们已自建方案（Playwright 真实 Chrome + 知乎自家 JS 签名 + 扫码登录，13 个 CLI 命令全通），现找开源界现成成熟替代品做对比。
> 关键结论先行：**开源界没有比我们"全功能读+写 CLI"更强的现成方案**。读采集向有绝对头部 MediaCrawler（70k★），CLI 向有 opencli/AutoCLI（31k/3k★），知乎专用 CLI 有 BAIGUANGMEI/zhihu-cli（115★，功能与我们重叠度最高、甚至覆盖发布侧）。技术路线上 MediaCrawler 与我们自建方案几乎一致（Playwright 登录态 + JS 表达式取签名，不做硬逆向）。

---

## 结论摘要

1. **生态两极分化**：知乎工具生态 2026 年的活体项目分三类——①多平台采集框架（MediaCrawler 一枝独秀，70k★ 且本月仍在提交）；②通用「网站→CLI」适配器（opencli 31k★ / AutoCLI 3k★，知乎只是 55~79 个站点之一，深度有限）；③知乎专用工具（BAIGUANGMEI/zhihu-cli 115★，纯 requests + 二维码 API，读+写功能最全，但轻维护、star 量级小）。
2. **技术路线趋同**：头部方案全部放弃硬 JS 逆向，主流两派——**Playwright 浏览器上下文取签名**（MediaCrawler，与我们相同）和**复用已登录浏览器会话**（opencli 系走 Chrome 扩展 + CDP；ZhihuVAPI 直接读本地 Chrome cookies）。纯协议派（zhihu-cli）靠官方二维码登录 API + 手动 Cookie + 统一 Chrome 指纹维持。
3. **历史项目大量死亡**：2016-2022 年间的知乎爬虫经典（fuck-login 5.9k★ 已归档、zhihu-login、wycm/zhihu-crawler、LiuRoy/zhihu_spider 等）全部停更，知乎 2023 年后的风控升级（x-zse-96 签名、扫码风控）杀死了整个纯逆向时代。
4. **对写操作（发布回答/文章/想法）的覆盖**：开源界几乎只有 BAIGUANGMEI/zhihu-cli 一家做到，且 2026-05 后停更。我们的 13 命令全通方案在读+写组合上处于生态空位的有利位置。
5. **无官方 API 路线**：知乎无公开开发者 API，所有方案均为逆向/浏览器自动化，长期看都有维护成本，自主可控的自建方案有存在价值。

---

## 方案清单

| 名称 | 链接 | Stars | 最后更新 | 路线 | 现状（2025-2026 可用性） |
|------|------|------:|----------|------|----------------|
| **MediaCrawler** | [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | 70,246 | 2026-11-05 | Playwright 登录态 + JS 表达式取签名 | ✅ 高度活跃，知乎问答/文章/评论可爬；2026-02 修知乎评论分页 |
| **opencli** | [jackwener/opencli](https://github.com/jackwener/opencli) | 30,900 | 2026-11-05 | Chrome 扩展复用登录浏览器（CDP） | ✅ 活跃；`opencli zhihu search/hot`，知乎深度一般 |
| **AutoCLI** | [nashsu/AutoCLI](https://github.com/nashsu/AutoCLI) | 3,022 | 2026-04-20 | opencli 的 Rust 重写，单二进制 4.7MB | ⚠️ 半年未 push；`autocli zhihu hot` 1.77s，功能继承 opencli |
| **RSSHub** | [DIYgod/RSSHub](https://github.com/DIYgod/RSSHub) | 46,834 | 2026-11-05 | 路由式 HTTP 抓取 → RSS | ✅ 活跃；知乎热榜/问题/用户路由，只读、输出 RSS |
| **InfoSpider** | [kangvcar/InfoSpider](https://github.com/kangvcar/InfoSpider) | 8,268 | 2026-04-21 | 多源个人数据取回（含知乎） | ✅ 活跃；偏「取回自己的数据」，非通用爬虫 |
| **PythonSpiderNotes** | [lining0806/PythonSpiderNotes](https://github.com/lining0806/PythonSpiderNotes) | 7,485 | 2021-06-21 | 教程合集（含知乎 selenium） | ❌ 停更 5 年，仅历史参考价值 |
| **DecryptLogin** | [CharlesPikachu/DecryptLogin](https://github.com/CharlesPikachu/DecryptLogin) | 2,851 | 2024-08-09 | requests 模拟登录库（含知乎） | ⚠️ 停更 1 年+，知乎登录多半已失效 |
| **LittleCrawler** | [pbeenigg/LittleCrawler](https://github.com/pbeenigg/LittleCrawler) | 763 | 2026-08-09 | 小红书/知乎/闲鱼公开信息抓取 | ✅ 较新；知乎为副平台，深度待验证 |
| **MorganZhang100/zhihu-spider** | [link](https://github.com/MorganZhang100/zhihu-spider) | 720 | 2024-01-17 | Python spider | ⚠️ 停更近 2 年 |
| **qiaomu-opencli-skills** | [joeseesun/qiaomu-opencli-skills](https://github.com/joeseesun/qiaomu-opencli-skills) | 998 | 2026-04-09 | opencli 的 AI Agent skills 封装 | ✅ 活跃（skill 层），底层依赖 opencli |
| **ForgeRSS** | [tmwgsicp/ForgeRSS](https://github.com/tmwgsicp/ForgeRSS) | 151 | 2026-11-02 | 任意网站→RSS，反爬突破 | ✅ 活跃；知乎为支持站点之一 |
| **BAIGUANGMEI/zhihu-cli** | [link](https://github.com/BAIGUANGMEI/zhihu-cli) | 115 | 2026-05-04 | 纯 requests + 官方二维码登录 API + 统一 Chrome 指纹 | ⚠️ 功能最全的知乎专用 CLI（读+写），但半年未更新；PyPI `pyzhihu-cli` |
| **zhihu_spider_selenium** | [ZouJiu1/zhihu_spider_selenium](https://github.com/ZouJiu1/zhihu_spider_selenium) | 73 | 2025-05-01 | Selenium 爬个人主页想法/文章/回答 | ⚠️ 小众可用，范围窄 |
| **ZhihuVAPI** | [cheezone/ZhihuVAPI](https://github.com/cheezone/ZhihuVAPI) | 62 | 2025-08-03 | Python 库，读本地 Chrome cookies | ⚠️ 个人项目，OOP 封装优雅但维护弱 |
| **MatrixSeven/ZhihuSpider** | [link](https://github.com/MatrixSeven/ZhihuSpider) | 303 | 2025-06-07 | Java，可爬关注关系 | ⚠️ 个人项目 |
| zhihu-salt-novel-downloader | [72tpwd7n88-boop/...](https://github.com/72tpwd7n88-boop/zhihu-salt-novel-downloader) | 8 | 2026-06-11 | 盐选小说下载 CLI+GUI | ⚠️ 垂直 niche |
| fuck-login（已归档） | [xchaoinfo/fuck-login](https://github.com/xchaoinfo/fuck-login) | 5,875 | 2018 归档 | requests 模拟登录合集 | ❌ 归档，纯历史 |
| wycm/zhihu-crawler | [link](https://github.com/wycm/zhihu-crawler) | 918 | 2019-04 | Java 分布式 + 代理池 | ❌ 停更 7 年 |
| zkqiang/zhihu-login | [link](https://github.com/zkqiang/zhihu-login) | 355 | 2022-07 | requests 模拟登录 | ❌ 停更，登录逻辑已失效 |
| LiuRoy/zhihu_spider | [link](https://github.com/LiuRoy/zhihu_spider) | 1,292 | 2016-08 | 早期 Python 爬虫 | ❌ 化石级 |

---

## 头部方案技术细节

### 1️⃣ MediaCrawler（70k★，采集向绝对头部）

- **技术原理（README 原话）**：基于 Playwright 浏览器自动化框架登录保存登录态；**无需 JS 逆向**——利用保留登录态的浏览器上下文环境，通过 JS 表达式获取签名参数。
- **知乎功能覆盖**：关键词搜索 ✅ / 指定帖子（问答+文章）ID 爬取 ✅ / **二级评论** ✅ / 指定创作者主页 ✅ / 登录态缓存 ✅ / IP 代理池 ✅ / 评论词云图 ✅
- **架构**：每平台一个 `media_platform/zhihu/` 模块（client + core + help），支持 SQLite/MySQL/CSV/JSON 落盘；教学版 2026-07 起移除用户个人信息采集（ID 匿名 hash 化、昵称脱敏），合规意识强。
- **维护节奏**：知乎相关修复持续到 2026（02-28 修评论分页、04-21 修跨域 Cookie 过长、07-01 平台 ID 统一 String）。
- **商业模式**：开源版 + MediaCrawlerPro（断点续爬、多账号+IP 代理池、去 Playwright、AI Agent Skill 一键安装 OpenClaw/Claude Code 等）。
- **与我们关系**：**技术路线与我们自建方案同源**（Playwright + JS 表达式取签名），它验证了该路线的可持续性和规模化（70k★ 用户基础下的风控对抗经验）。

### 2️⃣ opencli / AutoCLI（31k / 3k★，通用 CLI 向）

- **定位**：「Make Any Website into CLI & Use your logged-in browser by AI agent」，79+ 站点适配器，知乎是其中之一（`opencli zhihu search/hot`）。
- **技术路线**：Chrome 扩展桥接已登录浏览器 → CDP 控制 → 复用会话，不管 token；声明式 YAML Pipeline 描述抓取流程，零代码加适配器；AI 生成适配器（`generate --ai`）。
- **AutoCLI**：opencli 的 Rust 重写，4.7MB 单文件零依赖，`zhihu hot` 实测 1.77s（比 Node 版快 11.6x）；103/122 命令测试通过（84%）。
- **短板**：知乎适配深度远不如专用工具——只有 search/hot 类浅层命令，无回答详情、无评论、无写操作；AutoCLI repo 本身 2026-04 后无 push（但上游 opencli 活跃）。
- **Agent 集成**：joeseesun/qiaomu-opencli-skills（998★）把 opencli 包成 Claude Code Skills，含自动诊断修复适配器（autofix）——分发模式值得参考。

### 3️⃣ BAIGUANGMEI/zhihu-cli（115★，知乎专用 CLI 最直接对标）

- **定位**：知乎命令行工具——搜索问题、查看回答、发布提问/想法/文章（图文混合富文本）、浏览热榜。PyPI 包名 `pyzhihu-cli`，已上架 ClawHub（`clawhub install pyzhihu-cli`）。
- **技术路线**：**纯 requests，无需 Playwright**；二维码登录走知乎官方 API `/api/v3/account/api/login/qrcode` + `qrcode` 库终端渲染（二维码同时落盘 `~/.zhihu-cli/login_qrcode.png` 供 Agent 读图发给用户扫码）；或手动粘贴 Cookie（z_c0/_xsrf/d_c0）。
- **反爬策略**：全局统一 Chrome 指纹（UA + sec-ch-ua + sec-ch-ua-platform 一致，版本号集中于 `config.CHROME_VERSION` 单点管理）；写操作带 CSRF（`_xsrf`/`x-xsrftoken`）。
- **功能矩阵（与我们重叠度最高）**：login/logout/status/whoami、search（问题/回答/文章/topic/people）、hot（带 N 条回答）、question、answer（--comments）、user/user-answers/user-articles、followers/following、**feed/feeds（首页推荐+评论）**、topic、vote、follow-question、**ask/pin/article（发布）**、delete-question/pin/article、collections、notifications；全部数据命令支持 `--json`。
- **状态**：2026-05-04 最后提交（v0.2.4），半年未更新，9 个 open issues；功能覆盖面甚至超过我们当前 13 命令（多 feed/通知/收藏/删除），但轻维护是风险。

### 4️⃣ ZhihuVAPI（62★，Python 库的差异化样本）

- **路线**：pip 库（非 CLI），**直接读取本地 Chrome/Cent 浏览器 cookies 文件**（Windows 优先，Mac 需手动配 cookie + 用户 hash），OOP 封装 `zhihu.People/Answer/Question`，迭代器式取列表（`for a in self.answers(5)`）。
- **价值**：cookie 复用思路最轻（连浏览器自动化都省了），API 设计优雅；但个人项目、2025-08 停更、跨平台 cookie 路径是硬伤。

### 5️⃣ 其余值得一提

- **RSSHub**（46.8k★）：知乎热榜/问题/用户/专栏 RSS 路由，只读场景的「零维护替代」——若我们只要监控类需求，RSS 比爬虫稳定。
- **LittleCrawler**（763★，2026-08 活跃）：小红书为主、知乎为辅的新生代多平台爬虫，可观察其知乎模块存活率作为风控晴雨表。
- **ForgeRSS**（151★，2026-11 活跃）：任意网站→RSS 含反爬突破，知乎在支持列表。

---

## 对我们自建方案的借鉴点

1. **MediaCrawler（路线验证 + 工程化）**
   - 同源路线（Playwright + JS 取签名）被 70k★ 项目验证可持续 → 我们的技术选型无需动摇。
   - 可借鉴：**二级评论爬取**、登录态缓存的组织方式、IP 代理池接口、落盘多后端（SQLite/MySQL/CSV）、教学版的数据脱敏合规做法。
   - 其 Pro 版的「断点续爬、多账号」是我们长任务场景的未来方向。
2. **zhihu-cli（功能矩阵 + 轻量认证）**
   - 功能对标清单：我们缺的 **feed/推荐流、notifications、collections、话题详情、删除操作、发布想法（pin）** 可参考其命令设计补齐。
   - `--json` 全命令覆盖、热榜直接带 N 条回答的 UX 值得抄。
   - **统一 Chrome 指纹单点管理**（`config.CHROME_VERSION`）是低成本反风控手段，我们 Playwright 方案可在真实 Chrome 指纹基础上对照检查一致性。
   - **二维码落盘给 Agent 发图**的模式与我们 Agent 场景天然契合，已验证可行。
3. **opencli/AutoCLI（分发与架构）**
   - 声明式 YAML pipeline 加适配器的思路，可类比我们把 13 命令沉淀为可配置的技能描述。
   - 多格式输出（table/json/yaml/csv/markdown）是比单纯 --json 更完整的 CLI 素养。
   - ClawHub Skill 分发模式（zhihu-cli 与 qiaomu-opencli-skills 都在用）是我们未来给 OpenClaw 生态贡献的现成路径。
4. **ZhihuVAPI（cookie 复用兜底）**
   - 直接读本机 Chrome cookies 作为 Playwright 失效时的轻量降级链路，值得留作 plan B。

## 推荐行动

| 优先级 | 行动 | 理由 |
|--------|------|------|
| P0 | **继续自建，不替换**：读采集可引用 MediaCrawler 的 zhihu 模块做交叉验证，写操作（发布/互动）开源界无可替代者 | 生态空位确认；替换无任何收益 |
| P1 | **pip 安装 `pyzhihu-cli` 实测**：对照我们 13 命令跑一遍功能矩阵，重点测它半年未更后（2026-11）登录与发布是否还活着 | 拿到「专用 CLI 存活率」第一手数据，同时白嫖其命令设计 |
| P1 | **克隆 MediaCrawler 精读 `media_platform/zhihu/`**：对比签名获取（JS 表达式 vs 我们的实现）、评论分页、登录态缓存细节 | 同源路线互相校验，吸收其风控对抗修复（2026-02 评论分页修复等） |
| P2 | 补功能缺口：feed/通知/收藏/话题/删除，按 zhihu-cli 矩阵补齐；`--json` 标准化 | 把我们 CLI 从「全通」推向「全覆盖」 |
| P2 | 输出侧增强：二级评论、词云、断点续爬（长任务） | MediaCrawler(Pro) 验证过的方向 |
| P3 | 分发沉淀：打包 PyPI + 写 ClawHub Skill | zhihu-cli/qiaomu 验证过的生态位，扩大影响力 |
| P3 | 监控 RSSHub 知乎路由 + LittleCrawler 存活状态作为风控晴雨表 | 低成本预警知乎反爬升级 |

---

### 附：调研方法记录

- GitHub Search API 关键词：`zhihu`（9,925 结果）/ `zhihu-cli` / `zhihu crawler` / `知乎爬虫` / `zhihu spider` / `zhihu downloader`，按 stars 排序取头部。
- 深挖对象：stars>100 或 2025-2026 仍有 push 的项目，逐一查 README、pushed_at、近期 commits、issues 关键词。
- 共消耗 19 次 HTTP 请求（预算 ≤20），未触发 DuckDuckGo 兜底。
- 局限：未实际运行任何候选工具（运行验证属下一阶段）；Gitee/知乎专栏等墙内生态未覆盖。
