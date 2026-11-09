#!/usr/bin/env python3
"""
comment_harvester.py — 高赞回答评论批量收割器(纯 HTTP 通道, zhihu_fast.FastClient)

== 定位 ==
批量收割「一个高赞问题的 top-N 赞回答的评论」。输入问题 id → 从 harvester.py
的库(harvest.sqlite3 answers 表)取 top-N 赞回答 → 逐个收割根评论 → 存 SQLite
→ 可选灌入 kb.py(FTS5 全文检索)。

== 评论 API 机制(2026-11-06 实测, 见 research/e3_评论收割.md) ==
- 端点: /api/v4/comment_v5/answers/{aid}/root_comment?order_by=score&limit=20&offset=0
- 纯 HTTP + x-zse-96 签名直打 → 200(登录态 cookies.json)。无需浏览器。
- limit=20 被服务端钳到 10 条/页(paging.next 内 limit=10)。
- 分页 = cursor 链: paging.next 的 offset 是编码三元组(如 575893761_11385165154_0),
  非数字偏移——和 feeds 同律: 只沿 paging.next 走, 不手工构造。
- common_counts.total_counts = 该回答根评论总数。

== 节流/熔断(与 harvester.py 同纪律) ==
- FastClient min_interval=5.0(高斯抖动, 均值≈6s), 间隔硬下限 5s;
- 40362/40301/429 → FastClient 内建指数退避 1m/5m/30m, 耗尽抛 ZhihuFastBlocked → 硬停;
- 验证码/跳登录 → 硬停(禁自动重试);
- 网络层瞬断 → 指数退避重试同 URL, 连续 5 次才硬停。

== 幂等/断点 ==
- comments.comment_id 主键去重(INSERT OR IGNORE);
- comment_state 按 answer_id 存 next_url/fetched/status, 中断重跑即续。

用法:
  python3 comment_harvester.py 19550225 --top 5 --per-answer 20   # 收割
  python3 comment_harvester.py 19550225 --top 5 --kb              # 收割+灌入 kb
  python3 comment_harvester.py 19550225 --status                  # 看进度
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
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
OUTPUT_DIR = TOOL_DIR / "output"

SCHEMA = """
CREATE TABLE IF NOT EXISTS comments(
  comment_id TEXT PRIMARY KEY,
  answer_id TEXT, question_id TEXT,
  parent_id TEXT,
  author_name TEXT, author_token TEXT,
  content TEXT, content_html TEXT,
  like_count INTEGER, child_count INTEGER,
  created_time INTEGER,
  rank_order INTEGER,
  raw TEXT, fetched_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_comments_answer ON comments(answer_id);
CREATE TABLE IF NOT EXISTS comment_state(
  answer_id TEXT, k TEXT, v TEXT,
  PRIMARY KEY(answer_id, k));
"""

ROOT_COMMENT = ("/api/v4/comment_v5/answers/{aid}/root_comment"
                "?order_by=score&limit={limit}&offset=0")


def _strip_html(html: str) -> str:
    """与 zhihu.py 一致的 HTML→纯文本(br→换行, </p>→空行, 去标签, 实体反转义)"""
    txt = re.sub(r"<br\s*/?>", "\n", html or "")
    txt = re.sub(r"</p>", "\n\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return _html.unescape(txt)


def _path_q(url: str) -> str:
    p = urlparse(url)
    return p.path + ("?" + p.query if p.query else "")


class CommentHarvester:
    def __init__(self, qid: str, db_path: Path = DEFAULT_DB,
                 per_answer: int = 20, limit: int = 20,
                 max_pages_per_answer: int = 10):
        self.qid = str(qid)
        self.per_answer = per_answer
        self.limit = limit
        self.max_pages = max_pages_per_answer
        self.db = sqlite3.connect(str(db_path))
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.client = FastClient(min_interval=5.0)   # 均值≈6s, 硬下限 5s
        self.requests = 0
        self.net_errors = 0

    # ---------- comment_state 存取 ----------
    def _sget(self, aid: str, k: str, default=None):
        r = self.db.execute(
            "SELECT v FROM comment_state WHERE answer_id=? AND k=?",
            (aid, k)).fetchone()
        return r[0] if r else default

    def _sset(self, aid: str, k: str, v):
        self.db.execute(
            "INSERT INTO comment_state(answer_id,k,v) VALUES(?,?,?) "
            "ON CONFLICT(answer_id,k) DO UPDATE SET v=excluded.v",
            (aid, k, json.dumps(v) if not isinstance(v, str) else v))
        self.db.commit()

    # ---------- top-N 赞回答 ----------
    def top_answers(self, n: int) -> list[tuple]:
        """从 harvester 库取该问题 top-N 赞回答 → [(aid, voteup, comment_count, author)]"""
        return self.db.execute(
            "SELECT answer_id, voteup_count, comment_count, author_name "
            "FROM answers WHERE question_id=? "
            "ORDER BY voteup_count DESC LIMIT ?",
            (self.qid, n)).fetchall()

    def count_comments(self, aid: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM comments WHERE answer_id=? AND parent_id IS NULL",
            (aid,)).fetchone()[0]

    # ---------- 单回答评论收割(cursor 链) ----------
    def harvest_answer(self, aid: str) -> dict:
        """收割一个回答的前 per_answer 条根评论。幂等: 已够数即跳过。"""
        have = self.count_comments(aid)
        if have >= self.per_answer:
            return {"answer_id": aid, "status": "done", "new": 0,
                    "total_in_db": have, "reason": f"已有 {have} 条(够数)"}
        next_url = self._sget(aid, "next_url")
        url = next_url or ROOT_COMMENT.format(aid=aid, limit=self.limit)
        fetched_new, pages = 0, 0
        status, reason = "done", ""
        rank = have                                # 续抓时排名接着走
        while have + fetched_new < self.per_answer and pages < self.max_pages:
            rec = self._fetch_page(aid, url, rank)
            if rec is None:                        # 硬停(状态已存)
                return {"answer_id": aid, "status": self._sget(aid, "status", "blocked"),
                        "new": fetched_new, "total_in_db": self.count_comments(aid),
                        "reason": self._sget(aid, "block_reason", "hard-stop")}
            pages += 1
            fetched_new += rec["n_new"]
            rank += rec["n_items"]
            url = rec["next_url"]
            self._sset(aid, "next_url", url or "")
            if rec["is_end"]:
                reason = f"is_end(自然到底, {pages} 页)"
                break
            if not url:
                status, reason = "stalled", "paging.next 缺失但 is_end=false"
                break
        else:
            reason = (f"够数({self.per_answer})" if have + fetched_new >= self.per_answer
                      else f"页数上限({self.max_pages})")
        total = self.count_comments(aid)
        if not reason:
            reason = f"够数({self.per_answer})"
        self._sset(aid, "status", status)
        self._sset(aid, "fetched_at", str(int(time.time())))
        return {"answer_id": aid, "status": status, "new": fetched_new,
                "total_in_db": total, "pages": pages, "reason": reason}

    def _fetch_page(self, aid: str, url: str, rank0: int) -> dict | None:
        """抓一页根评论并入库。返回 {n_items, n_new, is_end, next_url}。"""
        t0 = time.monotonic()
        try:
            d = self.client.get(_path_q(url) if url.startswith("http") else url)
        except ZhihuFastBlocked as e:
            self._sset(aid, "status", "blocked")
            self._sset(aid, "block_reason", str(e)[:200])
            print(f"[hard-stop] aid={aid} {e}", flush=True)
            return None
        except httpx.HTTPError as e:
            self.net_errors += 1
            backoff = min(60.0, 10.0 * self.net_errors)
            print(f"[net-error {self.net_errors}/5] aid={aid} "
                  f"{type(e).__name__}: {str(e)[:100]} → {backoff:.0f}s 后重试",
                  flush=True)
            if self.net_errors >= 5:
                self._sset(aid, "status", "net_error")
                self._sset(aid, "block_reason", str(e)[:200])
                return None
            time.sleep(backoff)
            return self._fetch_page(aid, url, rank0)
        finally:
            self.requests += 1
        self.net_errors = 0
        lat = round((time.monotonic() - t0) * 1000)
        data = d.get("data") or []
        paging = d.get("paging") or {}
        rows = []
        for i, c in enumerate(data):
            cid = str(c.get("id") or "")
            if not cid or cid == "None":
                continue
            au = c.get("author") or {}
            html_content = c.get("content") or ""
            rows.append((cid, aid, self.qid, None,
                         au.get("name"), au.get("url_token"),
                         _strip_html(html_content), html_content,
                         c.get("like_count"), c.get("child_comment_count"),
                         c.get("created_time"), rank0 + i,
                         json.dumps(c, ensure_ascii=False), int(time.time())))
        n_before = self.count_comments(aid)
        self.db.executemany(
            "INSERT OR IGNORE INTO comments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
        self.db.commit()
        n_new = self.count_comments(aid) - n_before
        is_end = bool(paging.get("is_end"))
        print(f"[page] aid={aid} items={len(rows)} new={n_new} "
              f"累计={self.count_comments(aid)} lat={lat}ms is_end={is_end}",
              flush=True)
        return {"n_items": len(rows), "n_new": n_new,
                "is_end": is_end, "next_url": paging.get("next")}

    # ---------- 主流程 ----------
    def run(self, top_n: int) -> dict:
        tops = self.top_answers(top_n)
        if not tops:
            print(f"[err] 库中无 qid={self.qid} 的回答, 先跑 harvester.py", flush=True)
            return {"qid": self.qid, "status": "no_answers"}
        print(f"[plan] qid={self.qid} top-{len(tops)} 赞回答, "
              f"每回答前 {self.per_answer} 条根评论", flush=True)
        results = []
        try:
            for aid, voteup, cc, author in tops:
                r = self.harvest_answer(aid)
                r.update({"voteup": voteup, "comment_count_declared": cc,
                          "author": author})
                results.append(r)
                print(f"[answer] {author}(赞{voteup}/宣称评论{cc}) "
                      f"→ 新收 {r['new']} 条, 库内 {r['total_in_db']} 条 "
                      f"[{r['status']}: {r.get('reason','')}]", flush=True)
                if r["status"] in ("blocked", "net_error"):
                    break                          # 硬停: 不再动下一个回答
        except KeyboardInterrupt:
            print("[interrupt] 键盘中断(断点已存, 重跑即续)", flush=True)
        finally:
            self.client.close()
        total_new = sum(r["new"] for r in results)
        total_db = sum(r["total_in_db"] for r in results)
        rep = {"qid": self.qid, "answers_processed": len(results),
               "comments_new": total_new, "comments_in_db": total_db,
               "requests": self.requests, "per_answer": results}
        print(f"[report] {json.dumps(rep, ensure_ascii=False)}", flush=True)
        return rep

    # ---------- kb 桥接 ----------
    def export_kb_json(self, aids: list[str] | None = None) -> list[Path]:
        """把已收评论写成 kb.py 可 ingest 的 output/comments_answers_{aid}.json"""
        OUTPUT_DIR.mkdir(exist_ok=True)
        if aids is None:
            aids = [r[0] for r in self.db.execute(
                "SELECT DISTINCT answer_id FROM comments WHERE question_id=?",
                (self.qid,)).fetchall()]
        paths = []
        for aid in aids:
            rows = self.db.execute(
                "SELECT comment_id, author_name, author_token, content, "
                "like_count, created_time, child_count, raw "
                "FROM comments WHERE answer_id=? AND parent_id IS NULL "
                "ORDER BY rank_order", (aid,)).fetchall()
            if not rows:
                continue
            comments = [{"id": r[0], "author": r[1], "author_token": r[2],
                         "content": r[3], "like_count": r[4], "created": r[5],
                         "child_count": r[6]} for r in rows]
            doc = {"type": "comments", "target": f"answers/{aid}",
                   "count": len(comments), "comments": comments}
            p = OUTPUT_DIR / f"comments_answers_{aid}.json"
            p.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
            paths.append(p)
        return paths


def main():
    ap = argparse.ArgumentParser(description="高赞回答评论批量收割器")
    ap.add_argument("qid")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--top", type=int, default=5, help="top-N 赞回答(默认 5)")
    ap.add_argument("--per-answer", type=int, default=20,
                    help="每回答收割根评论数(默认 20)")
    ap.add_argument("--kb", action="store_true",
                    help="收割后导出 output/*.json 并灌入 kb.py")
    ap.add_argument("--status", action="store_true", help="只看进度")
    args = ap.parse_args()

    db_path = Path(args.db)
    if args.status:
        conn = sqlite3.connect(str(db_path))
        conn.executescript(SCHEMA)
        n = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE question_id=?", (args.qid,)).fetchone()[0]
        per = conn.execute(
            "SELECT c.answer_id, a.author_name, a.voteup_count, COUNT(*) "
            "FROM comments c LEFT JOIN answers a ON a.answer_id=c.answer_id "
            "WHERE c.question_id=? GROUP BY c.answer_id "
            "ORDER BY a.voteup_count DESC", (args.qid,)).fetchall()
        print(json.dumps({"qid": args.qid, "comments_total": n,
                          "per_answer": [{"aid": r[0], "author": r[1],
                                          "voteup": r[2], "comments": r[3]}
                                         for r in per]},
                         ensure_ascii=False, indent=1))
        conn.close()
        return

    hv = CommentHarvester(args.qid, db_path=db_path, per_answer=args.per_answer)
    rep = hv.run(args.top)
    if args.kb and rep.get("comments_in_db"):
        paths = hv.export_kb_json()
        print(f"[kb] 导出 {len(paths)} 个 JSON → {OUTPUT_DIR}", flush=True)
        import kb
        conn = kb.open_db()
        for p in paths:
            s = kb.ingest_file(conn, p)
            print(f"[kb] {p.name}: {s}", flush=True)
        print(f"[kb] stats: {kb.stats(conn)}", flush=True)
        conn.close()


if __name__ == "__main__":
    main()
