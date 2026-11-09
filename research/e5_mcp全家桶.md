# e5 · MCP 全家桶 — zhihu_tool 完整能力的 MCP 暴露

> 2026-11-06 · 无人区原创 · 纯本地
> 目标:把 zhihu_tool 已建成能力(抓取/金丝雀/监控/知识库)全部暴露为 MCP 工具,
> AI 代理经 stdio JSON-RPC 一站式调用,不再手敲 CLI。

## 1. 背景与边界

- 基座: `mcp_server.py` v1.1.0(7 工具: question/question_feeds/search/user/
  user_answers/status/kb_search),零第三方依赖,JSON-RPC 2.0 over stdio。
- 待暴露能力(同事已建成的 CLI):
  - `harvester.py` 全量抓取(库 = 工具根目录 `harvest.sqlite3`,**非** harvester/ 子目录)
  - `canary.py` 风控姿态金丝雀(落盘 `canary/`)
  - `watch.py` 油茶情报监控(落盘 `watch/`)
  - `kb.py` 知识库(stats 子命令)
  - `exporter.py` —— **不存在,跳过**(任务书允许:同事没做完就不暴露)

## 2. 工具清单(13 = 旧 7 + 新 6)

| # | 工具 | 性质 | 耗时 | 说明 |
|---|------|------|------|------|
| 1-7 | (旧) zhihu_question / question_feeds / search / user / user_answers / status / kb_search | 同步直连 | 秒级 | 不动 |
| 8 | zhihu_harvest_start(qid, fresh?) | **后台派生** | 立即返回 | Popen 起 harvester.py,记 pid+log;已跑则不重复起 |
| 9 | zhihu_harvest_status(qid) | 同步查库 | <1s | harvester.py --status + runner 存活态 |
| 10 | zhihu_canary_run(qid?) | 同步慢任务 | ~60-90s | 匿名金丝雀,budget 20 请求 |
| 11 | zhihu_watch_run | 同步慢任务 | ~30-60s | 5 关键词 × 1 搜索请求 |
| 12 | zhihu_watch_new(since_hours?) | 同步查库 | <1s | watch.py new |
| 13 | zhihu_kb_stats | 同步查库 | <1s | kb.py stats |

### 设计要点
- **长任务不阻塞**: harvest 是小时级任务,start 只 Popen 返回 pid,
  绝不 wait;防僵尸 = 收割线程 `Thread(target=p.wait, daemon=True)`
  + `/proc/<pid>/stat` 状态判 zombie。
- **重复启动防护**: pidfile(`harvest_logs/<qid>.pid`)+ 存活检查,
  已跑返回 `started:false, reason:already_running`。
- **子进程风格照抄旧 kb_search**: `subprocess.run([sys.executable, cli, ...],
  capture_output, text, timeout, cwd=TOOL_DIR)`,抽公共函数 `_run_cli`。
- description 全部写明耗时与代价,供 AI 选型。

## 3. 冒烟记录

(待填: initialize → tools/list → 各新工具 call,timeout 保护)
