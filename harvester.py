#!/usr/bin/env python3
"""
harvester.py — 知乎问题全量回答抓取调度器(纯 HTTP 通道, zhihu_fast.FastClient)

== 分页机制(2026-11-06 实测, 见 research/d1_全量抓取调度器.md) ==
feeds API 分页 = cursor 链: 响应 paging.next 内含 cursor + session_id + offset(页序号)。
手工 bump offset(无 session_id) → 服务器忽略, 永远回第 1 页(probe: offset
0/100/500/1000/1400 全返回相同 first/last id)。→ 必须沿 paging.next 链走;
断点续抓存的是 next_url, 不是数字 offset。

== 断点续抓 ==
SQLite 两表: answers(answer_id 主键去重) + state(next_url/status 等)。
中断(Ctrl-C/异常/预算)后重跑同命令即 resume:
  - 沿存下的 next_url 继续;
  - 若响应是"第 1 页数据"(session_id 过期) → 自动开新 session 从 0 重走,
    去重集合保证不重复入库, 追到前沿(n_new>0)后恢复正常规则。

== 自适应节流(均值≥5s 硬约束) ==
  - 基础间隔 6.0s, FastClient 高斯抖动(均值=interval, sigma≈1.2);
  - 顺利一页 → interval -0.2s(缓升, 下限 5.0s);
  - 空页/全重复页/异常 → interval ×1.5(上限 30s), stall+1;
  - 连续 3 次 stall → 休眠 300s 后原 URL 重试; 再 stall → 硬停;
  - 40362/40301/429 → FastClient 内建指数退避 1m/5m/30m, 耗尽抛 ZhihuFastBlocked
    → 存状态 status=blocked 硬停;
  - 验证码/跳登录/need_force_login → 硬停(禁自动重试)。

用法:
  python3 harvester.py 19550225                 # 开抓/断点续抓
  python3 harvester.py 19550225 --fresh         # 清空该问题进度重抓
  python3 harvester.py 19550225 --status        # 只看进度
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zhihu_fast import FastClient, ZhihuFastBlocked

TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_DB = TOOL_DIR / "harvest.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS answers(
  answer_id TEXT PRIMARY KEY,
  question_id TEXT,
  voteup_count INTEGER, comment_count INTEGER,
  author_name TEXT, author_token TEXT,
  created_time INTEGER, updated_time INTEGER,
  excerpt TEXT, url TEXT,
  raw TEXT, page_no INTEGER, fetched_at INTEGER);
CREATE TABLE IF NOT EXISTS pages(
  qid TEXT, page_no INTEGER,
  n_items INTEGER, n_new INTEGER, n_skip_type INTEGER,
  first_id TEXT, last_id TEXT, latency_ms INTEGER, fetched_at INTEGER,
  PRIMARY KEY(qid, page_no));
CREATE TABLE IF NOT EXISTS state(
  qid TEXT, k TEXT, v TEXT,
  PRIMARY KEY(qid, k));
"""

INITIAL = "/api/v4/questions/{qid}/feeds?limit={limit}&offset=0&order=default&platform=desktop"


def _path_q(url: str) -> str:
    p = urlparse(url)
    return p.path + ("?" + p.query if p.query else "")


class Harvester:
    def __init__(self, qid: str, db_path: Path = DEFAULT_DB,
                 limit: int = 20, base_interval: float = 6.0,
                 max_requests: int = 280, stall_sleep: float = 300.0):
        self.qid = str(qid)
        self.limit = limit
        self.interval = base_interval      # 当前间隔均值(秒), 硬下限 5.0
        self.max_requests = max_requests
        self.stall_sleep = stall_sleep
        self.db = sqlite3.connect(str(db_path))
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.client = FastClient(min_interval=5.0)   # UA 与 cookies.json 一致
        self.requests = 0
        self.stalls = 0
        self.slept_once = False            # 休眠 5 分钟只给一次机会
        self.rewalk = False                # session 过期后的去重重走模式
        self.net_errors = 0                # 连续网络层错误计数(瞬断不判死刑)

    # ---------- state 存取 ----------
    def _sget(self, k: str, default=None):
        r = self.db.execute("SELECT v FROM state WHERE qid=? AND k=?",
                            (self.qid, k)).fetchone()
        return r[0] if r else default

    def _sset(self, k: str, v):
        self.db.execute(
            "INSERT INTO state(qid,k,v) VALUES(?,?,?) "
            "ON CONFLICT(qid,k) DO UPDATE SET v=excluded.v",
            (self.qid, k, json.dumps(v) if not isinstance(v, str) else v))
        self.db.commit()

    def count(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM answers WHERE question_id=?",
            (self.qid,)).fetchone()[0]

    # ---------- 主循环 ----------
    def run(self) -> dict:
        status = self._sget("status", "running")
        if status == "done":
            return self._report("done", "已完成, 无需重跑(--fresh 可重抓)")
        next_url = self._sget("next_url")
        page_no = int(self._sget("page_no", "0"))
        if next_url:
            print(f"[resume] 从断点恢复: page_no={page_no} 已抓 {self.count()} 条",
                  flush=True)
        url = next_url or INITIAL.format(qid=self.qid, limit=self.limit)
        reason = "is_end"
        try:
            while True:
                if self.requests >= self.max_requests:
                    reason = f"budget(单轮请求上限 {self.max_requests})"
                    status = "budget"
                    break
                rec = self._fetch_page(url, page_no + 1)
                if rec is None:               # 硬停信号已在 _fetch_page 内处理
                    status = self._sget("status", "blocked")
                    reason = status
                    break
                page_no += 1
                url = rec["next_url"]
                self._sset("page_no", str(page_no))
                self._sset("next_url", url or "")
                if rec["is_end"]:
                    reason = (f"is_end(自然到底, 共 {page_no} 页 "
                              f"{self.count()} 条)")
                    status = "done"
                    break
                if rec.get("need_force_login"):
                    reason = "need_force_login(登录墙)"
                    status = "login_wall"
                    break
                if not url:                   # 没给 next 又非 is_end —— 异常形态
                    reason = "paging.next 缺失但 is_end=false"
                    status = "stalled"
                    break
        except KeyboardInterrupt:
            reason = "键盘中断(断点已存, 重跑即续)"
            status = self._sget("status", "running")
        except Exception as e:               # 兜底: 任何意外异常不丢状态
            reason = f"未捕获异常: {type(e).__name__}: {str(e)[:150]}"
            status = "error"
        finally:
            self._sset("status", status)
            self._sset("ended_at", str(int(time.time())))
            self.client.close()
        return self._report(status, reason)

    # ---------- 单页抓取(含节流/熔断/去重) ----------
    def _fetch_page(self, url: str, page_no: int) -> dict | None:
        # 节流: 让 FastClient 的高斯 throttle 均值 = self.interval
        self.client.min_interval = max(1.0, self.interval - 1.0)
        t0 = time.monotonic()
        try:
            d = self.client.get(_path_q(url) if url.startswith("http") else url)
        except ZhihuFastBlocked as e:
            # FastClient 已完成 1m/5m/30m 指数退避仍失败 / 硬信号
            self._sset("status", "blocked")
            self._sset("block_reason", str(e)[:200])
            print(f"[hard-stop] {e}", flush=True)
            return None
        except httpx.HTTPError as e:
            # 网络层瞬断(DNS/超时/连接重置) —— 非风控,计 stall 退避重试同 URL;
            # 连续 5 次才硬停(给 WSL/网关抖动容错;断点已存,重跑也可续)
            self.net_errors += 1
            backoff = min(60.0, 10.0 * self.net_errors)
            print(f"[net-error {self.net_errors}/5] {type(e).__name__}: "
                  f"{str(e)[:100]} → {backoff:.0f}s 后重试同 URL", flush=True)
            if self.net_errors >= 5:
                self._sset("status", "net_error")
                self._sset("block_reason", str(e)[:200])
                return None
            time.sleep(backoff)
            return self._fetch_page(url, page_no)
        finally:
            self.requests += 1
        self.net_errors = 0                 # 成功一页即清零
        lat = round((time.monotonic() - t0) * 1000)
        data = d.get("data") or []
        paging = d.get("paging") or {}
        ids, rows, n_skip = [], [], 0
        for item in data:
            tgt = item.get("target") or {}
            if item.get("target_type") != "answer" and tgt.get("type") != "answer":
                n_skip += 1
                continue
            aid = str(tgt.get("id"))
            ids.append(aid)
            author = tgt.get("author") or {}
            rows.append((aid, self.qid,
                         tgt.get("voteup_count"), tgt.get("comment_count"),
                         author.get("name"), author.get("url_token"),
                         tgt.get("created_time"), tgt.get("updated_time"),
                         (tgt.get("excerpt") or "")[:500], tgt.get("url"),
                         json.dumps(tgt, ensure_ascii=False), page_no,
                         int(time.time())))
        n_before = self.count()
        self.db.executemany(
            "INSERT OR IGNORE INTO answers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
        self.db.commit()
        n_new = self.count() - n_before

        # --- session 过期检测: 深断点 resume 却返回第 1 页数据 ---
        page0 = json.loads(self._sget("page0_ids", "[]"))
        if page_no == 1 and not page0:
            self._sset("page0_ids", json.dumps(ids))
            page0 = ids
        if (not self.rewalk and page0 and ids
                and set(ids) == set(page0) and page_no > 2):
            print("[session] next_url 返回第 1 页数据 → session 过期, "
                  "开新 session 去重重走", flush=True)
            self.rewalk = True
            self._sset("next_url",
                       INITIAL.format(qid=self.qid, limit=self.limit))
            self._sset("page0_ids", "[]")
            return {"is_end": False, "next_url":
                    INITIAL.format(qid=self.qid, limit=self.limit),
                    "need_force_login": False}
        if self.rewalk and n_new > 0:
            print(f"[session] 已追到前沿(page={page_no}), 恢复正常规则", flush=True)
            self.rewalk = False

        # --- 节流自适应 ---
        is_end = bool(paging.get("is_end"))
        stall = (not rows and not is_end) or \
                (n_new == 0 and rows and not self.rewalk and not is_end)
        if stall:
            self.stalls += 1
            self.interval = min(30.0, self.interval * 1.5)
            print(f"[stall {self.stalls}/3] page={page_no} n_items={len(rows)} "
                  f"n_new={n_new} → 间隔升至 {self.interval:.1f}s", flush=True)
            if self.stalls >= 3:
                if self.slept_once:
                    self._sset("status", "stalled")
                    print("[hard-stop] 休眠后再 stall, 硬停", flush=True)
                    return None
                self.slept_once = True
                print(f"[stall] 连续 3 次, 休眠 {self.stall_sleep:.0f}s…", flush=True)
                time.sleep(self.stall_sleep)
                self.stalls = 0
        else:
            self.stalls = 0
            self.slept_once = False
            self.interval = max(5.0, self.interval - 0.2)

        self.db.execute(
            "INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?,?,?,?,?)",
            (self.qid, page_no, len(rows), n_new, n_skip,
             ids[0] if ids else None, ids[-1] if ids else None,
             lat, int(time.time())))
        self.db.commit()
        print(f"[page {page_no}] items={len(rows)} new={n_new} "
              f"累计={self.count()} lat={lat}ms 间隔={self.interval:.1f}s "
              f"is_end={is_end}", flush=True)
        return {"is_end": is_end,
                "next_url": paging.get("next"),
                "need_force_login": bool(paging.get("need_force_login"))}

    def _report(self, status: str, reason: str) -> dict:
        rep = {"qid": self.qid, "status": status, "reason": reason,
               "answers": self.count(), "requests_this_run": self.requests,
               "final_interval": round(self.interval, 2)}
        print(f"[report] {json.dumps(rep, ensure_ascii=False)}", flush=True)
        return rep


def main():
    ap = argparse.ArgumentParser(description="知乎问题全量回答抓取调度器")
    ap.add_argument("qid")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--interval", type=float, default=6.0,
                    help="基础间隔均值(秒), 下限 5.0")
    ap.add_argument("--max-requests", type=int, default=280)
    ap.add_argument("--fresh", action="store_true", help="清空该问题进度重抓")
    ap.add_argument("--status", action="store_true", help="只看进度")
    args = ap.parse_args()

    db_path = Path(args.db)
    if args.fresh:
        conn = sqlite3.connect(str(db_path))
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM answers WHERE question_id=?", (args.qid,))
        conn.execute("DELETE FROM pages WHERE qid=?", (args.qid,))
        conn.execute("DELETE FROM state WHERE qid=?", (args.qid,))
        conn.commit()
        conn.close()
        print(f"[fresh] 已清空 qid={args.qid} 的进度")
    if args.status:
        conn = sqlite3.connect(str(db_path))
        conn.executescript(SCHEMA)
        n = conn.execute("SELECT COUNT(*) FROM answers WHERE question_id=?",
                         (args.qid,)).fetchone()[0]
        st = dict(conn.execute("SELECT k,v FROM state WHERE qid=?",
                               (args.qid,)).fetchall())
        pg = conn.execute("SELECT MAX(page_no), COUNT(*) FROM pages WHERE qid=?",
                          (args.qid,)).fetchone()
        print(json.dumps({"answers": n, "max_page": pg[0], "pages_rows": pg[1],
                          "state": st}, ensure_ascii=False, indent=1))
        conn.close()
        return
    hv = Harvester(args.qid, db_path=db_path, limit=args.limit,
                   base_interval=max(5.0, args.interval),
                   max_requests=args.max_requests)
    hv.run()


if __name__ == "__main__":
    main()
