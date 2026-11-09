#!/usr/bin/env python3
"""
mcp_server.py — 知乎工具的 MCP(stdio) 服务层: 供 AI 代理直接调用
协议: JSON-RPC 2.0 over stdio, 每行一个消息(MCP 标准 stdio 传输)
零第三方依赖(仅用标准库 + 本目录 zhihu_fast.py 的 httpx)——手机容器友好

工具集(全部走纯 HTTP 签名通道):
  zhihu_question(qid)                    问题元数据
  zhihu_question_feeds(qid,limit,offset) 问题回答翻页
  zhihu_search(query,limit,offset)       搜索
  zhihu_user(token)                      用户信息
  zhihu_user_answers(token,limit,offset) 用户回答列表
  zhihu_status()                         通道状态
  zhihu_kb_search(query,limit)           本地知识库全文检索
  ---- 调度/长任务类(e5, 2026-11-06) ----
  zhihu_harvest_start(qid,fresh?)        全量抓取后台启动(立即返 pid,不阻塞)
  zhihu_harvest_status(qid)              抓取进度查询(快)
  zhihu_canary_run(qid?)                 风控姿态金丝雀(慢,60-90s)
  zhihu_watch_run                        油茶情报监控一轮(慢,30-60s)
  zhihu_watch_new(since_hours?)          监控新增条目查询(快)
  zhihu_kb_stats                         知识库条目统计(快)

注册示例(openclaw/claude):  command: python3 /path/zhihu_tool/mcp_server.py
"""
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zhihu_fast import FastClient, ZhihuFastBlocked  # noqa: E402

SERVER_INFO = {"name": "zhihu-tool", "version": "1.2.0"}
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "zhihu_question",
        "description": "获取知乎问题元数据(标题/描述/回答数/关注数)",
        "inputSchema": {
            "type": "object",
            "properties": {"qid": {"type": "string", "description": "问题ID"}},
            "required": ["qid"],
        },
    },
    {
        "name": "zhihu_question_feeds",
        "description": "获取知乎问题的回答列表(翻页)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "qid": {"type": "string", "description": "问题ID"},
                "limit": {"type": "integer", "default": 5},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["qid"],
        },
    },
    {
        "name": "zhihu_search",
        "description": "知乎搜索(回答/文章混合结果)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["query"],
        },
    },
    {
        "name": "zhihu_user",
        "description": "获取知乎用户信息(粉丝/简介/创作统计)",
        "inputSchema": {
            "type": "object",
            "properties": {"token": {"type": "string", "description": "用户url_token"}},
            "required": ["token"],
        },
    },
    {
        "name": "zhihu_user_answers",
        "description": "获取用户的回答列表",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["token"],
        },
    },
    {
        "name": "zhihu_status",
        "description": "查看知乎通道状态(登录态/cookie/日用量)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "zhihu_kb_search",
        "description": "在本地知乎知识库中全文检索已抓取的内容(中文二字组索引,支持问题/回答/文章/评论)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "zhihu_harvest_start",
        "description": (
            "启动知乎问题的全量回答抓取(后台进程,立即返回 pid,不阻塞)。"
            "harvest 是长任务(自适应节流均值≥5s/页,千条级问题以小时计),"
            "支持断点续抓:同 qid 重跑即续;已在跑时重复调用不会起第二个进程。"
            "结果入 harvest.sqlite3。进度用 zhihu_harvest_status 查询。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "qid": {"type": "string", "description": "问题ID"},
                "fresh": {"type": "boolean", "default": False,
                          "description": "true=清空该问题已有进度从头重抓"},
            },
            "required": ["qid"],
        },
    },
    {
        "name": "zhihu_harvest_status",
        "description": (
            "查询某问题全量抓取的进度(已抓条数/页码/状态 "
            "running|done|blocked|stalled 等 + 后台进程 pid 与存活态)。"
            "快,只读本地 SQLite,不发网络请求。"),
        "inputSchema": {
            "type": "object",
            "properties": {"qid": {"type": "string", "description": "问题ID"}},
            "required": ["qid"],
        },
    },
    {
        "name": "zhihu_canary_run",
        "description": (
            "跑一次知乎风控姿态金丝雀:匿名探测挑战页/签名可用性/错误码矩阵/"
            "登录墙严格度/边缘头/前端版本常量,与上次运行 diff 并按 "
            "CRITICAL/HIGH/MEDIUM/LOW/INFO 分级告警(CRITICAL=签名算法换代=快通道死)。"
            "慢:约 15-20 个请求,耗时 60-90 秒,调用方 timeout 需 ≥180s。"
            "纯匿名探测,不耗登录态日预算。"),
        "inputSchema": {
            "type": "object",
            "properties": {"qid": {"type": "string",
                                     "description": "活体靶问题ID(缺省用工具内建常量)"}},
        },
    },
    {
        "name": "zhihu_watch_run",
        "description": (
            "跑一轮油茶行业情报监控:5 个关键词(山茶油/油茶/油茶籽/油茶产业/茶油价格)"
            "各搜 1 次,共 5 个请求(计登录态日预算),快照落库并按条目 id 判新,"
            "返回新增清单。慢:约 30-60 秒,调用方 timeout 需 ≥120s。"),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "zhihu_watch_new",
        "description": (
            "查询情报监控最近 N 小时的新增条目(关键词/类型/标题/赞数/URL/发现时间)。"
            "快,纯查本地库;需先跑过 zhihu_watch_run 才有数据。"),
        "inputSchema": {
            "type": "object",
            "properties": {"since_hours": {"type": "integer", "default": 24,
                                             "description": "回溯小时数"}},
        },
    },
    {
        "name": "zhihu_kb_stats",
        "description": (
            "本地知乎知识库条目统计(questions/answers/articles/comments/users/"
            "FTS 索引行数)。快,纯查库。"),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_client: FastClient | None = None


def get_client() -> FastClient:
    global _client
    if _client is None:
        _client = FastClient(min_interval=3.0)
    return _client


# ===================== 调度/长任务类工具(e5) =====================
TOOL_DIR = Path(__file__).resolve().parent
HARVEST_LOG_DIR = TOOL_DIR / "harvest_logs"


def _run_cli(argv: list, timeout: float) -> str:
    """跑本目录 CLI 并返回其 stdout(与旧 kb_search 同风格的 subprocess 直调)。
    非零退出码不抛异常,包成 JSON 交还给调用方判断。"""
    r = subprocess.run([sys.executable, str(TOOL_DIR / argv[0]), *argv[1:]],
                       capture_output=True, text=True, timeout=timeout,
                       cwd=str(TOOL_DIR))
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        return json.dumps({"ok": False, "exit_code": r.returncode,
                           "stdout": out[-3000:],
                           "stderr": (r.stderr or "").strip()[-3000:]},
                          ensure_ascii=False)
    return out


def _pid_alive(pid: int) -> bool:
    """存活且非僵尸。优先读 /proc/<pid>/stat 状态位(Linux/WSL);
    读不到再退 os.kill(pid,0) 探测。"""
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[-1].split()[0]
        return state != "Z"
    except FileNotFoundError:
        return False
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _harvest_start(args: dict) -> str:
    """Popen 后台起 harvester.py,立即返回 pid。pidfile 防重复启动;
    收割线程 + /proc 僵尸判定,保证已退出的进程不被误判为在跑。"""
    qid = str(args["qid"])
    fresh = bool(args.get("fresh"))
    if not re.fullmatch(r"[0-9]{1,20}", qid):
        return json.dumps({"started": False, "reason": "bad_qid",
                           "hint": "qid 必须是纯数字问题 ID"}, ensure_ascii=False)
    HARVEST_LOG_DIR.mkdir(exist_ok=True)
    pidfile = HARVEST_LOG_DIR / f"{qid}.pid"
    logfile = HARVEST_LOG_DIR / f"{qid}.log"
    if pidfile.exists():
        try:
            old_pid = int(pidfile.read_text().strip())
        except ValueError:
            old_pid = None
        if old_pid and _pid_alive(old_pid):
            return json.dumps({
                "started": False, "reason": "already_running",
                "pid": old_pid, "qid": qid,
                "hint": "用 zhihu_harvest_status 查进度"}, ensure_ascii=False)
    if fresh:  # --clear 是清完即退的快操作,同步做完再起后台任务
        _run_cli(["harvester.py", qid, "--clear"], timeout=30)
    lf = open(logfile, "ab", buffering=0)
    try:
        p = subprocess.Popen(
            [sys.executable, str(TOOL_DIR / "harvester.py"), qid],
            stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            cwd=str(TOOL_DIR), start_new_session=True)
    finally:
        lf.close()  # 关闭父进程副本;子进程已继承 fd
    threading.Thread(target=p.wait, daemon=True).start()  # 收割防僵尸
    pidfile.write_text(str(p.pid))
    return json.dumps({
        "started": True, "pid": p.pid, "qid": qid, "fresh": fresh,
        "log": str(logfile),
        "hint": "后台抓取中(自适应节流均值≥5s/页);用 zhihu_harvest_status 查进度"},
        ensure_ascii=False)


def _harvest_status(qid: str) -> str:
    """harvester.py --status 的 JSON + runner(pid/存活态)附加字段。"""
    out = _run_cli(["harvester.py", str(qid), "--status"], timeout=30)
    try:
        rep = json.loads(out)
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "raw": out[-2000:]}, ensure_ascii=False)
    runner = None
    pidfile = HARVEST_LOG_DIR / f"{qid}.pid"
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            runner = {"pid": pid, "alive": _pid_alive(pid)}
        except ValueError:
            pass
    rep["runner"] = runner
    return json.dumps(rep, ensure_ascii=False)


def call_tool(name: str, args: dict) -> str:
    c = get_client()
    if name == "zhihu_question":
        return json.dumps(c.question(args["qid"]), ensure_ascii=False)
    if name == "zhihu_question_feeds":
        return json.dumps(
            c.question_feeds(args["qid"], args.get("limit", 5),
                             args.get("offset", 0)), ensure_ascii=False)
    if name == "zhihu_search":
        return json.dumps(
            c.search(args["query"], args.get("limit", 10),
                     args.get("offset", 0)), ensure_ascii=False)
    if name == "zhihu_user":
        return json.dumps(c.user(args["token"]), ensure_ascii=False)
    if name == "zhihu_user_answers":
        return json.dumps(
            c.user_answers(args["token"], args.get("limit", 5),
                           args.get("offset", 0)), ensure_ascii=False)
    if name == "zhihu_status":
        return json.dumps({
            "logged_in": "z_c0" in c.cookies,
            "cookies": sorted(c.cookies.keys()),
            "daily_count": c.daily_count,
            "daily_budget": c.daily_budget,
            "channel": "fast(纯HTTP签名)",
        }, ensure_ascii=False)
    if name == "zhihu_kb_search":
        r = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "kb.py"),
             "search", args["query"], "--limit", str(args.get("limit", 10)),
             "--json"],
            capture_output=True, text=True, timeout=60,
            cwd=str(Path(__file__).parent))
        out = r.stdout.strip()
        if r.returncode != 0 or not out:
            # kb.py 若无 --json 模式,退回纯文本输出
            r = subprocess.run(
                [sys.executable, str(Path(__file__).parent / "kb.py"),
                 "search", args["query"], "--limit", str(args.get("limit", 10))],
                capture_output=True, text=True, timeout=60,
                cwd=str(Path(__file__).parent))
            out = r.stdout.strip() or r.stderr.strip()
        return out
    if name == "zhihu_harvest_start":
        return _harvest_start(args)
    if name == "zhihu_harvest_status":
        return _harvest_status(args["qid"])
    if name == "zhihu_canary_run":
        argv = ["canary.py", "run"]
        if args.get("qid"):
            argv += ["--qid", str(args["qid"])]
        return _run_cli(argv, timeout=300)
    if name == "zhihu_watch_run":
        return _run_cli(["watch.py", "run"], timeout=240)
    if name == "zhihu_watch_new":
        return _run_cli(["watch.py", "new", "--since-hours",
                         str(args.get("since_hours", 24))], timeout=30)
    if name == "zhihu_kb_stats":
        return _run_cli(["kb.py", "stats"], timeout=30)
    raise ValueError(f"未知工具: {name}")


def respond(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    print(json.dumps(out, ensure_ascii=False), flush=True)


def handle(msg: dict):
    method = msg.get("method", "")
    msg_id = msg.get("id")
    try:
        if method == "initialize":
            respond(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })
        elif method == "notifications/initialized":
            pass  # 通知无需响应
        elif method == "tools/list":
            respond(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params", {})
            text = call_tool(params.get("name", ""),
                             params.get("arguments") or {})
            respond(msg_id, {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            })
        elif method == "ping":
            respond(msg_id, {})
        else:
            if msg_id is not None:
                respond(msg_id, error={"code": -32601,
                                       "message": f"Method not found: {method}"})
    except ZhihuFastBlocked as e:
        respond(msg_id, {
            "content": [{"type": "text", "text": f"[BLOCKED] {e}"}],
            "isError": True,
        })
    except Exception as e:
        if msg_id is not None:
            respond(msg_id, error={"code": -32603,
                                   "message": f"{type(e).__name__}: {e}"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(msg)


if __name__ == "__main__":
    main()
