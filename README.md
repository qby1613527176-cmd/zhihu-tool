# zhihu_tool — 知乎反爬突破工具集

> 2026-11-05 建成。解决 2026-07-17 Firecrawl 被 zse_ck 挑战挡住的历史遗留问题。

> ⚠️ **风控状态公告（2026-11-09 沙盒实测）**：知乎 11 月风控升级——匿名纯算通道**暂时失效**：
> `GET /explore` 不再种 d_c0（已修复降级 POST /udid）、`__zse_ck` 铸造结果被 unhuman 标记、扫码 image 接口 40352。
> **登录罐（PC 导出 cookies.json）完全不受影响，是当前唯一可靠通道**（无 __zse_ck 也全通）。
> 匿名 bootstrap 代码保留待用；恢复信号由 canary.py 金丝雀监控。

## 原理（为什么不逆向签名）

知乎的防线：
1. **EdgeOne Bot 管理**（`x-secng-*` 头）+ **zse_ck JS 挑战**（裸 HTTP 403）
2. **API 签名** `x-zse-96`（匿名裸调 → 403 `code:10003`）
3. **风控指纹**（无头浏览器默认 UA 含 `HeadlessChrome` → 403 `code:40362`）
4. **登录墙**（匿名: 问题只给 3 个回答；搜索/深翻页不发请求）

本工具**不逆向任何算法**：用真实 Chrome 跑知乎页面，让知乎自己的 JS 完成
挑战求解和请求签名，工具只在网络层收割响应 + 解析 SSR `js-initialData`。
→ 对签名算法轮换**永久免疫**。

## 文件

| 文件 | 作用 |
|---|---|
| `zhihu.py` | 浏览器核心库：`ZhihuSession`（浏览器/节流/解析/翻页拦截） |
| `zhihu_fast.py` | **纯 HTTP 通道**：x-zse-96 签名纯 Python 端口 + `FastClient`(httpx) + **匿名自举 bootstrap**（纯算铸 __zse_ck）——无浏览器可跑 |
| `fetch.py` | CLI：question / article / user / search / status / **bootstrap**（加 `--fast` 走纯 HTTP） |
| `mcp_server.py` | **MCP stdio 服务**：AI 代理直接调用（零依赖 JSON-RPC） |
| `login.py` | 登录态获取：`qr` 扫码 或 `cookies` 导入 |
| `vendor/py/` | zhihulite/zhihu_zse_ck 纯算铸 cookie 模块（保留原样，build 轮换免疫） |
| `harvester.py` | 全量回答调度器（**纯 HTTP** FastClient；cursor 链翻页+断点续抓+自适应节流+**include=content 全文入库**；实测 994/1528） |
| `corpus_hydrate.py` | 语料库全文补水（hydrated 标志位断点续补；目标集模式按优先级清单） |
| `comment_harvester.py` | 回答评论批量收割（comment_v5 纯 HTTP 直打） |
| `exporter.py` | 抓取结果 → Markdown 可读档案（HTML→MD 转换） |
| `health.py` | 会话健康探针（z_c0/匿名罐分级告警+自愈） |

## 配置

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `ZHIHU_CHROME_VERSION` | UA 的 Chrome 大版本号（zhihu.py / zhihu_fast.py 同源，防指纹漂移） | `150` |
| `canary.py` | 风控姿态金丝雀（每日防御指纹+diff告警） |
| `kb.py` | 抓取沉淀层（SQLite FTS5 中文二字组全文检索） |
| `watch.py` | 行业情报监控（关键词快照+新增 diff，油茶试点） |
| `zse_ck_solver.js` | __zse_ck 沙箱求解器（已被 vendor/py/ 纯算路线取代，留档研究） |
| `cookies.json` | 浏览器导出的 cookie（快通道复用，勿外传） |
| `.profile/` | 持久化浏览器档案（cookie/登录态，自动复用） |

## 双通道架构

| 通道 | 原理 | 适用 |
|---|---|---|
| **fast 纯 HTTP** | x-zse-96 纯 Python 签名(RSSHub v3 血统) + 纯算铸 __zse_ck(vendor/py zhihulite 血统) | **手机容器/高频小量/AI 调用**;question/feeds/user/user-answers/search;`bootstrap` 匿名自举免 PC |
| **browser 浏览器** | 真实 Chrome 让知乎自家 JS 签名 | 深翻页/评论/热榜/想法等全类型;快通道被挡时的兑底 |

## 用法

```bash
cd ~/.openclaw/workspace/zhihu_tool

# 登录(解锁深翻页/搜索)
python3 login.py qr              # 扫码(生成 /tmp/zhihu_login_qr.png)
python3 login.py cookies         # 或粘贴浏览器 cookie 字符串

# 全量抓取(cursor链+断点续抓)
python3 harvester.py 19550225

# 风控金丝雀(每日姿态指纹)
python3 canary.py run

# 知识库(灌入+中文全文检索)
python3 kb.py ingest out_dir/ && python3 kb.py search "山茶油"   # ⚠️ ingest 参数是目录(默认 output/),不是单个文件

# 情报监控(油茶关键词快照+新增diff)
python3 watch.py run && python3 watch.py new

# 采集(浏览器全功能)
python3 fetch.py question 19550225 --max 50 -o q.json

# 采集(纯HTTP快通道,无需浏览器)
python3 fetch.py --fast question 19550225
python3 fetch.py --fast search "山茶油" --max 10
python3 fetch.py --fast user xie-yu-han-95-30
python3 fetch.py --fast user-answers xie-yu-han-95-30 --max 10
python3 fetch.py --fast status

# AI 代理调用(MCP stdio)
python3 mcp_server.py
python3 fetch.py user xie-yu-han-95-30 -o u.json
python3 fetch.py search "山茶油 GRAS" --max 30 -o s.json   # 需登录
python3 fetch.py article 573461020 -o a.json               # 需登录
python3 fetch.py hot --max 30 -o hot.json                  # 热榜
python3 fetch.py comments 19550225 --aid 1991579825958303223 --max 50 [--children]  # 评论(+子评论)
python3 fetch.py user-answers xie-yu-han-95-30 --max 50    # 用户回答列表
python3 fetch.py user-articles xie-yu-han-95-30 --max 50   # 用户文章列表
python3 fetch.py user-followers <token> --max 50           # 粉丝列表
python3 fetch.py user-followees <token> --max 50           # 关注中列表
python3 fetch.py topic 19551147                            # 话题元数据
python3 fetch.py pins <token> --max 50                     # 想法(图文)
python3 fetch.py collections <token> --max 50              # 收藏夹列表
python3 fetch.py status          # 查看登录态/cookie
```

## 能力矩阵（2026-11-06 全部实测通过）

| 内容 | 匿名 | 登录后 |
|---|---|---|
| 问题元数据(标题/描述/回答数/关注数) | ✅ | ✅ |
| 回答正文 | 前 3 个 | ✅ 深翻页(单轮实测 135 个/18.7 万字) |
| 用户主页(粉丝/回答数/简介) | ✅ | ✅ |
| 搜索(文章+回答混合,含翻页) | ❌ 登录墙 | ✅ 实测 10/10 |
| 专栏文章(全文/作者/赞数) | 未验证 | ✅ 实测全文 1337 字 |
| 热榜(标题/热度/摘要/链接) | ❌ 登录墙 | ✅ 30 条全字段 |
| 评论(作者/赞数/翻页) | ❌ | ✅ 实测 30/1119(cursor 分页, totals 可见) |
| 子评论(--children) | ❌ | ✅ 点「N 条回复」自动展开收集 |
| 用户回答列表(标题/赞数/翻页) | ❌ | ✅ 实测 115 条 |
| 用户文章列表 | ❌ | ✅ SSR首页+滚动翻页 |
| 粉丝/关注中列表 | ❌ | ✅ SSR首页+滚动翻页 |
| 话题元数据(关注数/问题数/简介) | ❌ | ✅ 实测「生活」1436万关注 |
| 想法 pins(图文/赞数/翻页) | ❌ | ✅ 实测 10 条带图 |
| 收藏夹列表(标题/数量/公开性) | ❌ | ✅ 实测 6 个全量 |
| zvideo 视频页 | ❌ | ◐ 页面可加载(标题可取);播放元数据提取未做 |
| 收藏夹详情(内容列表) | ❌ | ❌ 数据路径未探明(entities 空) |
| 圆桌 | ❌ | ❌ 未找到活体靶子(索引页无链接) |

*评论结构备注: author 为扁平对象(name/url_token 直取);评论项 class=`CommentContent css-*`(无 CommentList 容器);cursor 分页(paging.next)。
*用户子列表(posts/followers/followees)首页数据走 SSR initialData,深翻页才发 API。

## 已知边角状态(2026-11-06 探测)

- **zvideo**: `/zvideo/{id}` 页面可访问(实测标题「🌿山茶油到底是啥油？」);播放地址等元数据在页面内的位置未定位
- **收藏夹详情**: `/favlist/{id}` 页面加载但 entities 空,内容可能需交互触发,未探明
- **圆桌**: 索引页 `/roundtable` 无可解析链接(正则匹配到的「2026」是年份 404);真实圆桌 URL 未获得
- 以上三项有具体需求再攻坚;核心内容类型(问题/回答/文章/评论/用户/搜索/热榜/想法/收藏夹列表)已全覆盖

## 已知限制

- **深翻页节流**：翻页越深，前端每页延迟越长（阅读节奏门槛）。单轮实测深到 135 个回答后 stall 退出。全量 1500+ 需断点续抓机制（TODO: 按 `paging.next`/offset 存检查点，分段多轮磨）

## 翻页机制（实测结论）

- 回答分页 = 前端 IntersectionObserver 哨兵 → 签名 feeds API（5 条/页）
- **合成滚轮事件不可靠**（常被前端忽略/拦截），`PageDown` 同 ❌
- **可靠触发**：对最后一个回答元素 `scrollIntoView({block:'end'})` → 哨兵进入视口 → API 自动签发；备选 `End` 键
- 每次触发后轮询等数据（最长 8s），失败退 End 键再等 5s，连续 6 次无增长判定 stall
- 速率：~5 条/2-4s；全量 1500+ 回答约 15-25 分钟，用 `--budget` 放宽滚动预算

## 反爬对抗细节

- **UA 伪装**：`Chrome/150.0.0.0`（必须匹配本机 Chrome 大版本）
- **指纹清理**：`navigator.webdriver`/`plugins`/`languages`/`platform`/`chrome.runtime`
- **挑战等待**：每次导航后轮询 HTML 直到 zse_ck 挑战 reload 完成
- **节流**：导航最小间隔 3s + 随机抖动；滚动 1.2–2.2s 随机
- **风控识别**：`code:40362`（硬拒）→ 抛 `ZhihuBlocked`；遇验证码需人工介入

## 失败模式与对策

| 症状 | 原因 | 对策 |
|---|---|---|
| `40362` | 指纹露馅(常见: Chrome 升级后 UA 不匹配) | 更新 `zhihu.py` 里 UA 版本号 |
| 挑战循环不过 | EdgeOne 策略升级 | `--headed` + xvfb-run 有头模式 |
| 抓到 3 个回答就停 | 未登录 | `login.py` |
| 搜索 0 结果 | 未登录 | `login.py` |
| 验证码弹出 | 频率过高 | 降速/换 IP/静置 |
| 翻页中途 stall | 前端节流/竞态 | 自动退 End 键重试；`--budget` 加大预算 |
| Page.content 导航竞态 | 挑战 reload 撞抓取 | 已内建容错 |

## 边界与纪律

- 个人研究/存档用途；遵守知乎 ToS 与《数据安全法》《个人信息保护法》
- 不碰盐选付费内容；不大规模并发；单账号低速率
- 登录 cookie 只存本机 `.profile/`，不外传
- **合规要点（调研实锤）**：刑事红线三要素叠加=绕反爬+非公开数据/个人信息+数量获利门槛；知乎 robots.txt 全站 Disallow。本工具限低频/本地存储/不分发。详见 `research/10_合规与韧性.md`

## 开源生态调研（2026-11-06，10 路并行）

10 份子报告在 `research/`，综合结论在 `research/_综合评估.md`。要点：

- 我们的架构（真实浏览器+原生签名）= 行业事实标准（MediaCrawler 70k★ 同构）
- 生态空位：读+写全功能知乎 CLI（本工具正占此位）
- **可补强**：① patchright 原位替换手写 stealth ② 拷入纯 Python 签名端口作 fallback（算法 4 年未变）③ 风控五件套（高斯间隔/四级熔断/日预算）
- **证伪路线**：纯 HTTP+TLS 伪装 / 移动端 API / m 站轻量入口——都不用再试
- **预警雷达**：watch RSSHub issues + zhihu-plus-plus（知乎反爬变化先行指标）
