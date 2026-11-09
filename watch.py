#!/usr/bin/env python3
"""watch.py — 油茶行业知乎情报监控器(纯 HTTP 搜索通道)

监控源: zhihu_fast.FastClient.search(/api/v4/search_v3, 登录态 cookies.json)
关键词集: 山茶油 / 油茶 / 油茶籽 / 油茶产业 / 茶油价格
每轮: 每关键词取前 20 条 → 存 SQLite(watch/watch.db) → 与历史快照按 id 判新 → 新增清单

用法:
  python3 watch.py run [--limit 20] [--interval 5]   # 一轮真实快照+diff(5 请求)
  python3 watch.py demo-diff                         # 假数据 diff 演示(零请求,离线)
  python3 watch.py show [--keyword K] [--limit N]    # 看最近一轮快照
  python3 watch.py history                           # 快照轮次历史
  python3 watch.py new [--since-hours 24]            # 最近 N 小时的新增条目

退出码: 0 正常;2 风控硬信号(验证码/登录墙);3 用法错误
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))
from zhihu_fast import FastClient, ZhihuFastBlocked  # noqa: E402

WATCH_DIR = TOOL_DIR / "watch"
DB_PATH = WATCH_DIR / "watch.db"

KEYWORDS = ["山茶油", "油茶", "油茶籽", "油茶产业", "茶油价格"]

# 只保留这三类内容卡片;ai_zhida/scholar/pin_general/None 等噪声卡丢弃
KEEP_TYPES = {"answer", "article", "question"}

_EM = re.compile(r"</?em>")

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword     TEXT NOT NULL,
  taken_at    INTEGER NOT NULL,
  n_items     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
  keyword     TEXT NOT NULL,
  item_type   TEXT NOT NULL,
  item_id     TEXT NOT NULL,
  title       TEXT,
  author      TEXT,
  voteup      INTEGER,
  created     INTEGER,
  updated     INTEGER,
  url         TEXT,
  rank        INTEGER,
  PRIMARY KEY (snapshot_id, item_id)
);
CREATE TABLE IF NOT EXISTS seen (
  item_id             TEXT PRIMARY KEY,
  item_type           TEXT,
  title               TEXT,
  first_keyword       TEXT,
  first_seen_at       INTEGER,
  first_seen_snapshot INTEGER
);
CREATE TABLE IF NOT EXISTS new_events (
  snapshot_id INTEGER NOT NULL,
  keyword     TEXT,
  item_type   TEXT,
  item_id     TEXT NOT NULL,
  title       TEXT,
  voteup      INTEGER,
  url         TEXT,
  detected_at INTEGER NOT NULL,
  PRIMARY KEY (snapshot_id, item_id)
);
"""


def open_db(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def _clean(s: str | None) -> str | None:
    if s is None:
        return None
    return _EM.sub("", s).strip()


def parse_search(data: dict, keyword: str) -> list[dict]:
    """search_v3 原始响应 → 规范条目(过滤噪声卡,剥 <em> 高亮)"""
    out: list[dict] = []
    for it in data.get("data", []):
        obj = it.get("object") or {}
        t = obj.get("type")
        if t not in KEEP_TYPES:
            continue
        oid = str(obj.get("id") or "")
        if not oid:
            continue
        author = (obj.get("author") or {}).get("name")
        if t == "answer":
            q = obj.get("question") or {}
            title = _clean(q.get("name") or obj.get("title"))
            url = f"https://www.zhihu.com/answer/{oid}"
        elif t == "article":
            title = _clean(obj.get("title"))
            url = f"https://zhuanlan.zhihu.com/p/{oid}"
        else:  # question
            title = _clean(obj.get("title"))
            url = f"https://www.zhihu.com/question/{oid}"
        out.append({
            "keyword": keyword,
            "item_type": t,
            "item_id": oid,
            "title": title,
            "author": author,
            "voteup": obj.get("voteup_count"),
            "created": obj.get("created_time"),
            "updated": obj.get("updated_time"),
            "url": url,
        })
    return out


def save_snapshot(conn: sqlite3.Connection, keyword: str,
                  items: list[dict], taken_at: int | None = None) -> tuple[int, list[dict]]:
    """落一轮快照,按 id 判新。返回 (snapshot_id, 新增条目列表)"""
    taken_at = taken_at or int(time.time())
    cur = conn.cursor()
    cur.execute("INSERT INTO snapshots(keyword, taken_at, n_items) VALUES (?,?,?)",
                (keyword, taken_at, len(items)))
    sid = cur.lastrowid
    new_items: list[dict] = []
    for rank, it in enumerate(items):
        cur.execute(
            "INSERT OR REPLACE INTO items(snapshot_id, keyword, item_type, item_id,"
            " title, author, voteup, created, updated, url, rank)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sid, keyword, it["item_type"], it["item_id"], it["title"], it["author"],
             it["voteup"], it["created"], it["updated"], it["url"], rank))
        known = cur.execute("SELECT 1 FROM seen WHERE item_id=?",
                            (it["item_id"],)).fetchone()
        if not known:
            cur.execute(
                "INSERT INTO seen(item_id, item_type, title, first_keyword,"
                " first_seen_at, first_seen_snapshot) VALUES (?,?,?,?,?,?)",
                (it["item_id"], it["item_type"], it["title"], keyword, taken_at, sid))
            cur.execute(
                "INSERT INTO new_events(snapshot_id, keyword, item_type, item_id,"
                " title, voteup, url, detected_at) VALUES (?,?,?,?,?,?,?,?)",
                (sid, keyword, it["item_type"], it["item_id"], it["title"],
                 it["voteup"], it["url"], taken_at))
            new_items.append(it)
    conn.commit()
    return sid, new_items


def cmd_run(args) -> int:
    """一轮真实监控: 5 关键词 × 1 请求 = 5 请求,高斯间隔均值≈6s"""
    t0 = time.time()
    conn = open_db()
    all_new: list[dict] = []
    round_meta = {"taken_at": int(t0), "keywords": {}, "requests": 0}
    try:
        with FastClient(min_interval=args.interval) as client:
            for kw in KEYWORDS:
                try:
                    data = client.search(kw, limit=args.limit, offset=0)
                except ZhihuFastBlocked as e:
                    print(f"[BLOCKED] 关键词 {kw!r}: {e}", file=sys.stderr)
                    conn.close()
                    return 2
                round_meta["requests"] = client.daily_count
                items = parse_search(data, kw)
                sid, new_items = save_snapshot(conn, kw, items)
                all_new.extend(new_items)
                round_meta["keywords"][kw] = {
                    "snapshot_id": sid, "n_items": len(items),
                    "n_new": len(new_items),
                }
                print(f"[{kw}] 快照#{sid} 条目 {len(items)} 新增 {len(new_items)}")
    finally:
        conn.close()
    round_meta["elapsed_s"] = round(time.time() - t0, 1)
    round_meta["n_new_total"] = len(all_new)
    # 轮次报告落盘
    rounds_dir = WATCH_DIR / "rounds"
    rounds_dir.mkdir(parents=True, exist_ok=True)
    rp = rounds_dir / f"round_{round_meta['taken_at']}.json"
    rp.write_text(json.dumps({"meta": round_meta, "new_items": all_new},
                             ensure_ascii=False, indent=1))
    print(f"\n== 本轮 {round_meta['requests']} 请求 / "
          f"{round_meta['elapsed_s']}s / 新增 {len(all_new)} ==")
    for it in all_new[:30]:
        print(f"  + [{it['item_type']}] {it['title']} (赞{it['voteup']}) {it['url']}")
    print(f"[saved] {rp}")
    return 0


def cmd_demo_diff(args) -> int:
    """假数据 diff 演示: 造『昨天』快照,再喂『今天』快照(同 id 复现+新 id),零请求"""
    demo_db = WATCH_DIR / "demo.db"
    demo_db.unlink(missing_ok=True)
    conn = open_db(demo_db)
    base = int(time.time()) - 86400

    def mk(oid: str, title: str, voteup: int, kw: str = "山茶油") -> dict:
        return {"keyword": kw, "item_type": "answer", "item_id": oid,
                "title": title, "author": "演示", "voteup": voteup,
                "created": base, "updated": base,
                "url": f"https://www.zhihu.com/answer/{oid}"}

    yesterday = [mk("A1", "山茶油多少钱一斤?", 10),
                 mk("A2", "山茶油的营养价值有哪些?", 32),
                 mk("A3", "山茶油哪个牌子正宗?", 5)]
    today = [mk("A1", "山茶油多少钱一斤?", 15),      # 老条目复现(赞数涨了)
             mk("A2", "山茶油的营养价值有哪些?", 40),  # 老条目复现
             mk("A4", "2026 年山茶油新国标解读", 88),  # ← 新
             mk("A5", "油茶籽收购价行情周报", 12)]     # ← 新

    sid1, new1 = save_snapshot(conn, "山茶油", yesterday, taken_at=base)
    sid2, new2 = save_snapshot(conn, "山茶油", today, taken_at=base + 86400)
    conn.close()
    print(f"[demo] 昨日快照#{sid1}: {len(yesterday)} 条 → 新增 {len(new1)}(首轮全新,符合预期)")
    print(f"[demo] 今日快照#{sid2}: {len(today)} 条 → 新增 {len(new2)}")
    for it in new2:
        print(f"  + {it['item_id']} {it['title']} (赞{it['voteup']})")
    ok = {it["item_id"] for it in new2} == {"A4", "A5"}
    print(f"[demo] diff 判新逻辑: {'✓ 成立(仅 A4/A5 判新,A1/A2 复现未误报)' if ok else '✗ 不成立'}")
    print(f"[demo] 演示库: {demo_db}(可随时删)")
    return 0 if ok else 1


def cmd_show(args) -> int:
    conn = open_db()
    q = """SELECT i.keyword, i.item_type, i.item_id, i.title, i.voteup, i.url, s.taken_at
           FROM items i JOIN snapshots s ON s.snapshot_id=i.snapshot_id
           WHERE s.snapshot_id IN (SELECT MAX(snapshot_id) FROM snapshots GROUP BY keyword)"""
    params: list = []
    if args.keyword:
        q += " AND i.keyword=?"
        params.append(args.keyword)
    q += " ORDER BY s.taken_at DESC, i.keyword, i.rank LIMIT ?"
    params.append(args.limit)
    for kw, t, oid, title, v, url, ts in conn.execute(q, params):
        print(f"[{kw}] [{t}] {title} (赞{v}) {url}")
    conn.close()
    return 0


def cmd_history(args) -> int:
    conn = open_db()
    for sid, kw, ts, n in conn.execute(
            "SELECT snapshot_id, keyword, taken_at, n_items FROM snapshots"
            " ORDER BY snapshot_id DESC LIMIT 50"):
        print(f"#{sid} {time.strftime('%m-%d %H:%M', time.localtime(ts))} [{kw}] {n} 条")
    conn.close()
    return 0


def cmd_new(args) -> int:
    conn = open_db()
    since = int(time.time()) - args.since_hours * 3600
    rows = list(conn.execute(
        "SELECT keyword, item_type, title, voteup, url, detected_at FROM new_events"
        " WHERE detected_at>=? ORDER BY detected_at DESC", (since,)))
    print(f"最近 {args.since_hours}h 新增 {len(rows)} 条:")
    for kw, t, title, v, url, ts in rows:
        print(f"  {time.strftime('%m-%d %H:%M', time.localtime(ts))} [{kw}] [{t}] "
              f"{title} (赞{v}) {url}")
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="watch.py",
                                 description="油茶行业知乎情报监控器(纯HTTP搜索)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="一轮真实快照+diff(5 请求)")
    p.add_argument("--limit", type=int, default=20, help="每关键词取前 N 条(≤20 单页)")
    p.add_argument("--interval", type=float, default=5.0, help="请求最小间隔秒(高斯均值≈+1)")
    sub.add_parser("demo-diff", help="假数据 diff 演示(零请求)")
    p = sub.add_parser("show", help="看最近一轮快照")
    p.add_argument("--keyword"); p.add_argument("--limit", type=int, default=50)
    sub.add_parser("history", help="快照轮次历史")
    p = sub.add_parser("new", help="最近 N 小时新增条目")
    p.add_argument("--since-hours", type=int, default=24)
    args = ap.parse_args()
    return {"run": cmd_run, "demo-diff": cmd_demo_diff, "show": cmd_show,
            "history": cmd_history, "new": cmd_new}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
